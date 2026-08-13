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

from purple.evaluation.action_registry import ActionRegistryStore
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
    detection_event_ids: tuple[str, ...] = ()
    corroborating_sources: tuple[str, ...] = ()
    entry_accepted: bool = False


@dataclass(frozen=True)
class ActionResult:
    """一個註冊動作的評分結果——四態恰好其一。"""

    action_id: str
    state: ActionState
    level: EvidenceLevel | None
    gap: str | None
    reason: str
    event_ids: tuple[str, ...] = ()


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

    @classmethod
    def for_frozen_registry(
        cls,
        registry: ActionRegistryStore,
        exercise_id: str,
        source_registry: Registry,
        evidence_by_action: dict[str, ActionEvidence] | None = None,
        alert_volume: int = 0,
    ) -> "EvaluationService":
        """建構評分服務時，動作清單只能來自**凍結的** Action Registry（#90）。

        「分母只能由凍結清單推導，不得由現場事件反推」在 #21 只是約定 ——
        呼叫端直接把 tuple 傳給 `actions` 欄位，繞過凍結檢查完全合法。這裡
        把它變成機制：**唯一**建構清單的路徑是 `frozen_actions()`，未凍結
        時它自己就會拋 `RegistryNotFrozen`（與 `denominator()` 共用同一道
        檢查，兩者不會各自漂移——見 `action_registry.py` 的說明）。直接對
        `EvaluationService(actions=...)` 建構子傳手填 tuple 仍然可行（測試
        需要能注入假資料），但那不是這個工廠函式，呼叫端要主動繞過去。
        """
        frozen = registry.frozen_actions(exercise_id)
        return cls(
            actions=tuple((action.id, action.technique) for action in frozen),
            source_registry=source_registry,
            evidence_by_action=evidence_by_action or {},
            alert_volume=alert_volume,
        )

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
