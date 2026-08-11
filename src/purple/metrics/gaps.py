"""缺口分類 —— 純函數，ADR ③ 選 D1 的核心（票 08）。

一次紅隊動作沒有換來偵測，可能是兩種完全不同的失敗，混為一談會冤枉藍隊：

- **偵測缺口 (detection gap)**：來源健康、raw 遙測有這筆，但沒觸發告警。
  「看得到卻沒偵測到」。這是藍隊該補規則的地方。
- **可見性缺口 (visibility gap)**：來源健康，但這筆連 raw 都沒進來。
  「根本沒看到」。這是遙測覆蓋的問題。

再加兩種非缺口：
- `stale` 來源 → `unknown`（設備故障期間，無法判定，不進分母）
- 未部署來源 → `out_of_scope`（—，不是缺口，只標示範圍）

**這支分類器就是 D1 的證明。** 若 Falco 健康、syscall 有記錄、但規則被停用時，
分類結果不是 detection gap 而是 visibility gap，就代表我們實質退回 D3 ——
Falco 覆蓋範圍內的偵測缺口全部不可觀測。那一條測試紅了，架構就錯了。
"""

from __future__ import annotations

from enum import StrEnum

from purple.registry.source_registry import Registry, SourceState


class MissClass(StrEnum):
    HIT = "hit"                        # 有偵測，不是 miss
    DETECTION_GAP = "detection_gap"    # 看得到卻沒偵測到
    VISIBILITY_GAP = "visibility_gap"  # 來源健康但這筆沒進來
    UNKNOWN = "unknown"                # 來源 stale，無法判定
    OUT_OF_SCOPE = "out_of_scope"      # 來源未部署（—）


def classify_miss(
    *,
    detected: bool,
    source_state: SourceState,
    telemetry_present: bool,
) -> MissClass:
    """把一次紅隊動作的結果分類。

    參數刻意全部具名，避免「三個 bool/enum」在呼叫端排錯位。

    - `detected`：這次動作有沒有對應的偵測事件（Core Event 的 detection.hit）。
    - `source_state`：負責這次動作的遙測來源目前狀態（來自 §07 Source Registry）。
    - `telemetry_present`：raw 遙測裡有沒有這筆（來自 §06 Evidence resolver）。
      **只有在來源健康、且判定不是可見性缺口時才需要它。**
    """
    if detected:
        return MissClass.HIT

    # 來源狀態先決：沒部署不算缺口，掉線無法判定。這兩者都不該被說成藍隊漏了。
    if source_state is SourceState.ABSENT:
        return MissClass.OUT_OF_SCOPE
    if source_state is SourceState.STALE:
        return MissClass.UNKNOWN

    # 來源健康：關鍵分岔就在 raw 遙測在不在。
    # 有 raw 卻沒告警 → 偵測缺口（含「規則被停用」）。
    # 連 raw 都沒有 → 可見性缺口。
    if telemetry_present:
        return MissClass.DETECTION_GAP
    return MissClass.VISIBILITY_GAP


def classify_from_registry(
    *,
    registry: Registry,
    source_id: str,
    detected: bool,
    telemetry_present: bool,
) -> MissClass:
    """Production connector：source_state 必須取自 Registry，不接受呼叫端字面值。"""
    source = registry.get(source_id)
    if source is None:
        raise ValueError(f"registry {registry.scenario_id!r} 沒有 source {source_id!r}")
    return classify_miss(
        detected=detected,
        source_state=source.state,
        telemetry_present=telemetry_present,
    )


def counts_towards_coverage(miss: MissClass) -> bool:
    """哪些分類進 action coverage 分母。

    `unknown`（設備故障）與 `out_of_scope`（未部署）不進分母 ——
    把設備故障算進去等於冤枉藍隊（ADR ⑧ ⑫）。
    """
    return miss in (MissClass.HIT, MissClass.DETECTION_GAP, MissClass.VISIBILITY_GAP)
