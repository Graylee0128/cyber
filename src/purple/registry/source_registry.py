"""Source Registry 狀態機 —— 純函數，無 I/O。

系統必須分辨兩件本質不同的事（spec §3.1、ADR ⑧）：

    ❌  來源有部署，但沒有這筆事件   → 算進可見性缺口（state=stale）
    —   來源未部署                  → 不算缺口，只標示範圍（state=absent）

把「沒裝」與「裝了卻掉線」混為一談，會系統性冤枉藍隊：一個 crash 掉的來源
若靠部署狀態自動推導，就會從清單裡**消失**，被洗成「不在範圍內」而免責。

防止這件事的結構性設計：`expected` 是 scenario 定義的一個欄位（`ScenarioSource`），
不由 heartbeat 推導。本模組**只**走訪 scenario 宣告的來源清單，再對每個來源查
heartbeat；絕不反過來從 heartbeat 集合生出來源清單。因此一個掉線（無 heartbeat）
的來源仍會出現在 registry 裡並判為 `stale`，而不是憑空消失。

實際去收 heartbeat 的是別處的 I/O 層；這裡把（期望清單、heartbeat 資料、now）
判定成 registry 快照，所以毫秒內測得完，不需要起 docker（對照 `clock/skew.py`）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

#: heartbeat 上報間隔。每個來源理應每 30s 回報一次（spec §3.3）。
HEARTBEAT_INTERVAL_S: int = 30

#: 容忍窗。距離上次 heartbeat 超過此值即判 stale。
#: 90s = 3 × 間隔，容許偶發抖動漏掉一兩拍才宣告掉線，避免把正常 jitter 誤判成故障。
HEARTBEAT_TOLERANCE_S: int = 90


class SourceState(StrEnum):
    #: expected 且 heartbeat 在容忍窗內。Console 據有無事件顯示 ✅／❌。
    HEALTHY = "healthy"
    #: expected 但 heartbeat 逾時（或從未回報）。該時窗的動作要標 unknown。
    #: 刻意與 ABSENT 分開 —— 掉線不是「不在範圍」，不可洗成免責。
    STALE = "stale"
    #: not expected。不算缺口，Console 顯示 —。
    ABSENT = "absent"


@dataclass(frozen=True)
class ScenarioSource:
    """scenario 定義裡的一筆來源宣告。

    `expected` 是 scenario 決定的事實，**不從部署／heartbeat 推導**（ADR ⑧）。
    這個型別的存在本身就是那條界線：要判定就得先給一份 scenario 清單，
    無從只靠 heartbeat 生出來源。
    """

    id: str
    expected: bool = True


@dataclass(frozen=True)
class StaleInterval:
    """某來源失去覆蓋的區間 [start, end]。

    P2 的 Evaluation Engine 用它自動判 `unknown`（spec §3.4）：

        動作時間窗 ∩ 來源 stale 區間 ≠ ∅  →  該動作標 unknown

    `start` 是最後一次 heartbeat —— gap 天然從這裡開始（ADR ⑫）；
    `start is None` 代表從未回報，區間左端無下界（−∞），任何在 `end` 之前的
    動作都落在缺口內。`end` 是判定當下的 now。
    """

    source: str
    start: datetime | None
    end: datetime

    def intersects(self, window_start: datetime, window_end: datetime) -> bool:
        """動作時間窗 [window_start, window_end] 是否與本 stale 區間相交。"""
        _require_aware(window_start, self.source, "window_start")
        _require_aware(window_end, self.source, "window_end")
        if window_end < window_start:
            raise ValueError(
                f"{self.source}: 動作時間窗顛倒（{window_start} > {window_end}）"
            )
        # [start, end] ∩ [ws, we] ≠ ∅  ⇔  ws ≤ end 且（start is None 或 start ≤ we）
        if window_start > self.end:
            return False
        if self.start is not None and self.start > window_end:
            return False
        return True

    def reason(self) -> str:
        """自動填入的原因欄（spec §3.4）。heartbeat gap 天然就是原因。"""
        end = self.end.isoformat()
        if self.start is None:
            return f"{self.source} 直到 {end} 從未回報心跳"
        return f"{self.source} 於 {self.start.isoformat()}–{end} 無心跳"


@dataclass(frozen=True)
class SourceStatus:
    """單一來源在某個 now 的判定結果。對應 spec §3.3 sources[] 的一筆。"""

    id: str
    expected: bool
    last_heartbeat: datetime | None
    state: SourceState

    @property
    def is_stale(self) -> bool:
        return self.state is SourceState.STALE

    def as_dict(self) -> dict:
        """spec §3.3 形狀：欄位恰為 id / expected / last_heartbeat / state。"""
        return {
            "id": self.id,
            "expected": self.expected,
            "last_heartbeat": (
                self.last_heartbeat.isoformat() if self.last_heartbeat else None
            ),
            "state": self.state.value,
        }


@dataclass(frozen=True)
class Registry:
    """一個 scenario 的來源 registry 快照。"""

    scenario_id: str
    sources: tuple[SourceStatus, ...]
    now: datetime
    tolerance_s: float

    def get(self, source_id: str) -> SourceStatus | None:
        for source in self.sources:
            if source.id == source_id:
                return source
        return None

    def stale_interval(self, source_id: str) -> StaleInterval | None:
        """來源的 stale 區間；非 stale（或不存在）回 None。

        供 P2 與動作時間窗取交集判 `unknown`（spec §3.4）。
        """
        source = self.get(source_id)
        if source is None or source.state is not SourceState.STALE:
            return None
        return StaleInterval(source=source.id, start=source.last_heartbeat, end=self.now)

    def stale_intervals(self) -> tuple[StaleInterval, ...]:
        """所有 stale 來源的區間。"""
        intervals = (self.stale_interval(s.id) for s in self.sources if s.is_stale)
        return tuple(i for i in intervals if i is not None)

    def as_dict(self) -> dict:
        """spec §3.3 完整形狀。"""
        return {
            "scenario_id": self.scenario_id,
            "sources": [s.as_dict() for s in self.sources],
        }


def _require_aware(moment: datetime, source: str, label: str) -> None:
    """回絕 naive datetime —— 無時區時間戳是整個 codebase 要消滅的東西（見 skew.py）。"""
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{source}: {label} 缺少 timezone。無時區的時間戳不可默默當成 UTC。"
        )


def _decide(
    expected: bool,
    last_heartbeat: datetime | None,
    now: datetime,
    tolerance_s: float,
) -> SourceState:
    """單一來源的狀態判定（spec §3.3 狀態表）。"""
    # not expected 是第一判準：不在 scenario 範圍就是 absent，與有無 heartbeat 無關。
    if not expected:
        return SourceState.ABSENT
    # expected 卻從未回報 heartbeat → 逾時的極端情形 → stale，**不是** absent。
    if last_heartbeat is None:
        return SourceState.STALE
    age_s = (now - last_heartbeat).total_seconds()
    # 邊界含容忍窗上緣：age == tolerance 仍算 healthy（與 skew.py 的 <= 一致，明寫不留猜）。
    return SourceState.HEALTHY if age_s <= tolerance_s else SourceState.STALE


def evaluate_registry(
    scenario_id: str,
    scenario_sources: Sequence[ScenarioSource],
    heartbeats: Mapping[str, datetime],
    now: datetime,
    tolerance_s: float = HEARTBEAT_TOLERANCE_S,
) -> Registry:
    """把（scenario 期望清單、heartbeat 資料、now）判定成 registry 快照。

    - `scenario_sources` 是**唯一**的來源清單來源（ADR ⑧）：輸出恰好走訪這份清單，
      掉線的來源仍在其中並判為 stale，不會消失。heartbeat 集合裡若有不在清單上的
      來源，一律忽略 —— 它不在 scenario 範圍內。
    - 時間一律用 timezone-aware datetime；naive 會被回絕。now 由呼叫端餵入（不在此
      呼叫 `datetime.now()`），測試才可決定性重現。
    """
    _require_aware(now, "<registry>", "now")
    for source_id, stamp in heartbeats.items():
        _require_aware(stamp, source_id, "heartbeat")

    seen: set[str] = set()
    statuses: list[SourceStatus] = []
    for decl in scenario_sources:
        if decl.id in seen:
            raise ValueError(f"scenario 定義出現重複來源 {decl.id!r}")
        seen.add(decl.id)

        last = heartbeats.get(decl.id)
        state = _decide(decl.expected, last, now, tolerance_s)
        statuses.append(
            SourceStatus(
                id=decl.id,
                expected=decl.expected,
                last_heartbeat=last,
                state=state,
            )
        )

    return Registry(
        scenario_id=scenario_id,
        sources=tuple(statuses),
        now=now,
        tolerance_s=tolerance_s,
    )
