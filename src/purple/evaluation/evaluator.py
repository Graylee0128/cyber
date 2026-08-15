"""P2 Evaluation：把（Frozen Action Registry、Source Registry、evidence）判定成
`hit/miss/unknown/not_executed` 四態之一 —— 純函數，無 I/O（同 gaps.py／source_registry.py 慣例）。

判定輸入只有三種真資料（#21 acceptance）：
  1. 這個動作有沒有對應的偵測事件（`detection_event_ids`）
  2. 負責這個動作時間窗的來源當時是否健康（Source Registry + 時間窗交集）
  3. 這個動作時間窗內有沒有 raw 遙測（`telemetry_refs`）

`miss` 的 visibility/detection 兩分類直接復用 `purple.metrics.gaps`（票 08 的 D1 分類器），
不重造第二份規則 —— 那正是 spec 要消滅的東西。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from purple.metrics.gaps import MissClass, classify_miss
from purple.registry.source_registry import Registry, SourceState


class ActionState(StrEnum):
    HIT = "hit"
    MISS = "miss"
    UNKNOWN = "unknown"
    NOT_EXECUTED = "not_executed"


class EvidenceLevel(StrEnum):
    #: 僅原始訊號，或（不論來源數）技術本身不足以證明更高的宣稱（如 T1005）。
    C1 = "C1"
    #: 已確認，但只有單一來源——同一 collector 兩筆不算跨來源（#21 acceptance）。
    C2 = "C2"
    #: 已確認，且至少兩個獨立來源交叉支持。
    C3 = "C3"


#: 開啟敏感檔本身不是外流的證據；就算多來源都看到「開啟」，也只證明存取發生過。
#: 升級到「已證明外流」需要獨立的資料流出證據，這支評分器不生產那種證據。
_UNPROVABLE_EXFILTRATION_TECHNIQUES = frozenset({"T1005"})


@dataclass(frozen=True)
class ActionEvidence:
    """一個註冊動作的實測證據（來自 Core Event／Evidence resolver 的投影）。"""

    action_id: str
    executed_at: datetime
    window_end: datetime
    expected_sources: tuple[str, ...]
    telemetry_refs: tuple[str, ...] = ()
    #: 每個 expected source 在此動作時間窗內有沒有命中 marker 的 raw 遙測
    #: （#126：Purple Console 畫面二的 Telemetry 欄輸入）。與 `telemetry_refs`
    #: 同一次查詢的副產品，不是第二次遙測後端呼叫。
    telemetry_by_source: tuple[tuple[str, bool], ...] = ()
    detection_event_ids: tuple[str, ...] = ()
    corroborating_sources: tuple[str, ...] = ()
    entry_accepted: bool = False


@dataclass(frozen=True)
class SourceMark:
    """一個來源在此動作的健康度與有無 raw 遙測——`purple.console.drilldown`
    畫面二 Telemetry 欄的輸入形狀，故意跟它的 `sources` 參數同構
    （`(source_id, SourceState, event_present)`）。"""

    source_id: str
    state: SourceState
    event_present: bool


@dataclass(frozen=True)
class ActionResult:
    """一個註冊動作的評分結果——四態恰好其一。"""

    action_id: str
    state: ActionState
    level: EvidenceLevel | None
    gap: str | None
    reason: str
    event_ids: tuple[str, ...] = ()
    source_marks: tuple[SourceMark, ...] = ()


@dataclass(frozen=True)
class EvaluationMetrics:
    """action coverage／confirmation rate／alert volume／excluded counts 同進同出。

    刻意不提供 `detection_rate`——那個名字會把 gap 分類洗掉，是 #21 明文禁止的字面值。
    """

    action_coverage: float | None
    confirmation_rate: float | None
    alert_volume: int
    excluded_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "action_coverage": self.action_coverage,
            "confirmation_rate": self.confirmation_rate,
            "alert_volume": self.alert_volume,
            "excluded_counts": dict(self.excluded_counts),
        }


@dataclass
class EvaluationService:
    """把一份凍結的 action 清單（id, technique）對照證據，判成四態＋指標。"""

    actions: tuple[tuple[str, str], ...]
    source_registry: Registry
    evidence_by_action: dict[str, ActionEvidence] = field(default_factory=dict)
    alert_volume: int = 0

    def evaluate(self) -> list[ActionResult]:
        results: list[ActionResult] = []
        for action_id, technique in self.actions:
            evidence = self.evidence_by_action.get(action_id)
            if evidence is None:
                results.append(
                    ActionResult(
                        action_id=action_id,
                        state=ActionState.NOT_EXECUTED,
                        level=None,
                        gap=None,
                        reason="紅隊未執行此註冊動作",
                    )
                )
                continue

            marks = _source_marks(evidence, self.source_registry)

            if evidence.detection_event_ids:
                level, reason = _grade_evidence(technique, evidence)
                results.append(
                    ActionResult(
                        action_id=action_id,
                        state=ActionState.HIT,
                        level=level,
                        gap=None,
                        reason=reason,
                        event_ids=evidence.detection_event_ids,
                        source_marks=marks,
                    )
                )
                continue

            state, gap, reason = _classify_not_hit(evidence, self.source_registry)
            results.append(
                ActionResult(
                    action_id=action_id,
                    state=state,
                    level=None,
                    gap=gap,
                    reason=reason,
                    source_marks=marks,
                )
            )
        return results

    def metrics(self) -> EvaluationMetrics:
        results = self.evaluate()
        covered = [r for r in results if r.state in (ActionState.HIT, ActionState.MISS)]
        hits = [r for r in covered if r.state is ActionState.HIT]
        confirmed = [r for r in hits if r.level in (EvidenceLevel.C2, EvidenceLevel.C3)]

        # 分母（HIT+MISS）為 0 時回 None，不假裝 0%——unknown／not_executed
        # 已經被排除在分母外，不是「算出來剛好是 0」，是「根本沒有可算的東西」。
        action_coverage = (len(hits) / len(covered)) if covered else None
        confirmation_rate = (len(confirmed) / len(covered)) if covered else None

        return EvaluationMetrics(
            action_coverage=action_coverage,
            confirmation_rate=confirmation_rate,
            alert_volume=self.alert_volume,
            excluded_counts={
                "unknown": sum(1 for r in results if r.state is ActionState.UNKNOWN),
                "not_executed": sum(1 for r in results if r.state is ActionState.NOT_EXECUTED),
            },
        )


def _grade_evidence(technique: str, evidence: ActionEvidence) -> tuple[EvidenceLevel, str]:
    if technique in _UNPROVABLE_EXFILTRATION_TECHNIQUES:
        return (
            EvidenceLevel.C1,
            f"{technique} 僅證明對敏感檔案的存取行為，未證明內容外流；"
            "外流需要獨立的資料流出證據，不因來源數增加而自動升級。",
        )

    distinct_sources = set(evidence.corroborating_sources)
    if evidence.entry_accepted and len(distinct_sources) >= 2:
        return EvidenceLevel.C3, "至少兩個獨立來源交叉確認同一筆偵測。"
    if evidence.entry_accepted:
        return EvidenceLevel.C2, "已確認，但僅單一來源——同一 collector 的多筆事件不算跨來源。"
    return EvidenceLevel.C1, "僅有原始遙測訊號，尚未經過確認。"


def _source_marks(evidence: ActionEvidence, registry: Registry) -> tuple[SourceMark, ...]:
    """走訪這個動作的 expected sources，配上 registry 的健康度與有沒有這筆
    raw 遙測（#126）。只看 `expected_sources`——那份清單本身已經是
    scenario 宣告 `expected=True` 的子集（見 assembly.py），未宣告或
    `expected=False` 的來源在這個動作的脈絡下不構成訊號，不屬於這裡。"""
    present = dict(evidence.telemetry_by_source)
    marks: list[SourceMark] = []
    for source_id in evidence.expected_sources:
        status = registry.get(source_id)
        if status is None:
            continue
        marks.append(
            SourceMark(
                source_id=source_id,
                state=status.state,
                event_present=present.get(source_id, False),
            )
        )
    return tuple(marks)


def _classify_not_hit(
    evidence: ActionEvidence, registry: Registry
) -> tuple[ActionState, str | None, str]:
    stale_during_window: list[str] = []
    healthy_sources: list[str] = []

    for source_id in evidence.expected_sources:
        status = registry.get(source_id)
        if status is None:
            continue
        if status.state is SourceState.HEALTHY:
            healthy_sources.append(source_id)
            continue
        if status.state is SourceState.STALE:
            interval = registry.stale_interval(source_id)
            if interval is not None and interval.intersects(
                evidence.executed_at, evidence.window_end
            ):
                stale_during_window.append(source_id)

    if not healthy_sources:
        covered = ", ".join(stale_during_window or evidence.expected_sources)
        return (
            ActionState.UNKNOWN,
            None,
            f"{covered} 於動作時間窗內無心跳，無法判定是否偵測到",
        )

    telemetry_present = bool(evidence.telemetry_refs)
    miss_class = classify_miss(
        detected=False, source_state=SourceState.HEALTHY, telemetry_present=telemetry_present
    )
    reason = (
        "來源健康、raw 遙測有這筆，但沒有觸發偵測——偵測缺口"
        if miss_class is MissClass.DETECTION_GAP
        else "來源健康，但這筆連 raw 遙測都沒有進來——可見性缺口"
    )
    return ActionState.MISS, miss_class.value, reason
