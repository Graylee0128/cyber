"""Purple Console 畫面二 —— 單一 technique 下鑽投影（票 #26 / 原 #27）。

點畫面一某列進來，看那個動作的完整故事：遙測有哪些、有沒有規則命中、延遲多少。

**Telemetry 欄不是裝飾** —— 它就是 §3.4 兩種漏檢畫出來的樣子，判讀規則是機械的：

    全部 ❌   + Detection 空  → 可見性缺口（補收集來源）
    至少一 ✅ + Detection 空  → 偵測缺口（補規則）
    至少一 ✅ + Detection 命中 → hit，看 latency

**必須避開的坑：❌ 與 — 不可混用。**

    ✅ 有這筆事件
    ❌ 來源有部署、但沒有這筆事件   → 算進可見性缺口
    — 來源未部署                    → 不算缺口，只標示範圍

把「沒裝」算成「漏了」會系統性冤枉藍隊，所以 Telemetry 欄**由 source registry 動態產生**
（#18），不寫死。缺口分類直接沿用引擎的 `MissClass`（畫面與引擎不得各算各的）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from purple.metrics.gaps import MissClass
from purple.registry.source_registry import SourceState


class TelemetryMark(StrEnum):
    """Telemetry 欄的四種標記。❌ 與 — 分屬不同狀態，永不互換。"""

    PRESENT = "✅"       # healthy 且有這筆事件
    NO_EVENT = "❌"      # healthy 已部署、但沒有這筆事件 → 可見性缺口的來源
    NOT_DEPLOYED = "—"   # absent 未部署 → 不算缺口，只標範圍
    UNKNOWN = "⏳"       # stale 掉線 → 該時窗判不出來


def telemetry_mark(source_state: SourceState, *, event_present: bool) -> TelemetryMark:
    """單一來源在這次動作的 Telemetry 標記。

    未部署（absent）先決：不管有沒有事件都是 — ，它只標示範圍，不是缺口。
    掉線（stale）判不出來 → ⏳。健康來源才進入「有沒有這筆事件」的分岔。
    """
    if source_state is SourceState.ABSENT:
        return TelemetryMark.NOT_DEPLOYED
    if source_state is SourceState.STALE:
        return TelemetryMark.UNKNOWN
    return TelemetryMark.PRESENT if event_present else TelemetryMark.NO_EVENT


@dataclass(frozen=True)
class TelemetryRow:
    source_id: str
    mark: TelemetryMark


def read_gap(marks: list[TelemetryMark], *, detected: bool) -> MissClass:
    """把 Telemetry 欄 + 有無偵測，機械地讀成缺口分類（上表三條規則）。

    這與引擎的 `classify_miss` 是同一套語意，只是視角從單一來源升到整個 technique：
    — 的來源（未部署）不參與缺口判定，只有 healthy 來源決定可見性 vs 偵測缺口。
    因此把一個 ❌ 的來源改成 —（撤除部署），分類**不會惡化**（冤枉防護 AC）。
    """
    if detected:
        return MissClass.HIT

    deployed = [m for m in marks if m is not TelemetryMark.NOT_DEPLOYED]
    if not deployed:
        # 全部未部署：不是缺口，只是不在範圍。
        return MissClass.OUT_OF_SCOPE
    if all(m is TelemetryMark.UNKNOWN for m in deployed):
        # 已部署的來源全掉線 → 判不出來。
        return MissClass.UNKNOWN
    if any(m is TelemetryMark.PRESENT for m in deployed):
        # 有 raw 卻沒告警 → 偵測缺口（含規則被停用）。
        return MissClass.DETECTION_GAP
    # healthy 來源都沒有這筆事件 → 可見性缺口。
    return MissClass.VISIBILITY_GAP


@dataclass(frozen=True)
class Detection:
    """偵測結果。無命中時 rule 為 None、latency 也為 None（不填 0，0 會被誤讀成「零延遲」）。"""

    rule: str | None
    latency_ms: int | None


@dataclass(frozen=True)
class TechniqueDrilldown:
    """單一 technique 的下鑽視圖。

    延遲三個指標各有具名欄位（不用位置參數），避免 MTTD/MTTR/containment 串位（P2-5）。
    `gap` 是引擎算好的 MissClass 值（畫面不重算）；`display_gap` 供顯示一致性檢查。
    """

    technique_id: str
    observed_at: str | None
    telemetry: tuple[TelemetryRow, ...]
    detection: Detection
    gap: MissClass
    mttd_ms: int | None
    mttr_ms: int | None
    containment_ms: int | None

    @property
    def display_gap(self) -> MissClass:
        """由 Telemetry 欄機械讀出的缺口分類，應與引擎的 `gap` 一致。"""
        return read_gap(
            [row.mark for row in self.telemetry],
            detected=self.detection.rule is not None,
        )


def build_drilldown(
    *,
    technique_id: str,
    observed_at: str | None,
    sources: list[tuple[str, SourceState, bool]],
    detection: Detection,
    engine_gap: MissClass,
    mttd_ms: int | None = None,
    mttr_ms: int | None = None,
    containment_ms: int | None = None,
) -> TechniqueDrilldown:
    """組出下鑽視圖。`sources` 是 (source_id, state, event_present) 的清單，由 source
    registry + evidence resolver 提供 —— Telemetry 欄由此動態產生，不寫死。"""
    telemetry = tuple(
        TelemetryRow(source_id=sid, mark=telemetry_mark(state, event_present=present))
        for sid, state, present in sources
    )
    return TechniqueDrilldown(
        technique_id=technique_id,
        observed_at=observed_at,
        telemetry=telemetry,
        detection=detection,
        gap=engine_gap,
        mttd_ms=mttd_ms,
        mttr_ms=mttr_ms,
        containment_ms=containment_ms,
    )
