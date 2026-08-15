"""把真資料組裝成 `EvaluationService` —— #21 判定邏輯與實際儲存之間的接線（#90 Phase 2）。

`evaluator.py` 刻意是純函數：它接受一份動作清單與一份證據，然後判定。這支模組
負責產生那兩份輸入，而且只能從這些地方產生：

    動作清單    ← Action Registry 的**凍結**快照      （未凍結即拒絕評分）
    是否偵測到  ← Core Event 的 `action_id` 關聯       （不靠技法＋時間窗猜）
    來源健康度  ← Source Registry（heartbeat 實測）
    raw 遙測    ← Evidence backend，以動作 marker 過濾

四條線各有各的真相來源，沒有任何一條可以由呼叫端傳字面值進來 —— 這正是
「三個判定輸入皆取自真資料」在程式上的意思。

失敗一律往外拋，不降級：遙測後端連不上時**不**回「沒有 raw 遙測」，因為那會把
一次基礎設施故障寫成藍隊的可見性缺口。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from purple.evaluation.action_registry import ActionRegistryStore
from purple.evaluation.evaluator import ActionEvidence, EvaluationService
from purple.evidence.backends import EvidenceBackend, EvidenceQuery
from purple.evidence.resolver import telemetry_source
from purple.registry.source_registry import Registry
from purple.store.executions import ActionExecution


@dataclass(frozen=True)
class EvaluationAssembler:
    """讀四個真相來源，組出一個可評分的 `EvaluationService`。

    依賴以 duck-typing 注入（與 `EvidenceResolver` 同慣例）：

    - `registry`：`ActionRegistryStore`，提供凍結後的動作清單
    - `events`：需有 `detections_by_action(exercise_id)` 與 `alert_volume(exercise_id)`
    - `records`：Alert Record 儲存，需有 `by_id(event_id)`（判斷偵測來自哪個 collector）
    - `executions`：需有 `for_exercise(exercise_id)`（紅隊執行 ground truth）
    - `source_registry`：`scenario_id -> Registry` 的查詢 seam
      （production 用 `purple.registry.production.RegistryService.get`）
    - `telemetry`：`EvidenceBackend`，用來確認動作時間窗內有沒有 raw 遙測
    """

    registry: ActionRegistryStore
    events: Any
    records: Any
    executions: Any
    source_registry: Callable[[str], Registry]
    telemetry: EvidenceBackend

    def __post_init__(self) -> None:
        # 沒有遙測後端就沒有第三個判定輸入，而缺了它 detection gap 與
        # visibility gap 只能用猜的。寧可在組裝時就拒絕，也不要交出一份
        # 看起來完整、實際上有一整類判定是編的報告。
        if self.telemetry is None:
            raise ValueError(
                "evaluation 需要 telemetry backend：沒有 raw 遙測就分不出"
                "偵測缺口與可見性缺口，不得以「查不到」代替「沒有」"
            )

    def build(self, exercise_id: str) -> EvaluationService:
        """組出這場演練的評分服務。registry 未凍結時拋 `RegistryNotFrozen`。"""
        snapshot = self.registry.get(exercise_id)
        # 分母的唯一入口。先於任何證據查詢 —— 未凍結就不該有評分這回事。
        actions = self.registry.frozen_actions(exercise_id)

        source_registry = self.source_registry(snapshot.scenario_id)
        expected_sources = tuple(
            source.id for source in source_registry.sources if source.expected
        )

        detections = self.events.detections_by_action(exercise_id)
        executed = self.executions.for_exercise(exercise_id)

        evidence: dict[str, ActionEvidence] = {}
        for action in actions:
            execution = executed.get(action.id)
            if execution is None:
                # 沒有執行紀錄 → 留白。evaluator 看到缺席就判 not_executed；
                # 在這裡補一個空證據會把它洗成 miss，等於紅隊少跑一個動作就
                # 扣藍隊一分。
                continue
            evidence[action.id] = self._evidence_for(
                execution, expected_sources, detections.get(action.id, ())
            )

        return EvaluationService(
            actions=tuple((action.id, action.technique) for action in actions),
            source_registry=source_registry,
            evidence_by_action=evidence,
            alert_volume=self.events.alert_volume(exercise_id),
        )

    def _evidence_for(
        self,
        execution: ActionExecution,
        expected_sources: tuple[str, ...],
        detections: Sequence[dict[str, Any]],
    ) -> ActionEvidence:
        event_ids = tuple(dict.fromkeys(event["event_id"] for event in detections))
        refs, by_source = self._telemetry_detail(execution, expected_sources)
        return ActionEvidence(
            action_id=execution.action_id,
            executed_at=execution.executed_at,
            window_end=execution.window_end,
            expected_sources=expected_sources,
            telemetry_refs=refs,
            telemetry_by_source=by_source,
            detection_event_ids=event_ids,
            corroborating_sources=self._sources_behind(event_ids),
            # 「已確認」＝ 這個動作至少有一筆 firing 的偵測事件落地，而不只是
            # raw 遙測裡有可疑的一行。resolved-only 不算：那代表我們只看到收尾。
            entry_accepted=any(event.get("lifecycle") == "firing" for event in detections),
        )

    def _sources_behind(self, event_ids: tuple[str, ...]) -> tuple[str, ...]:
        """每筆偵測背後的遙測來源。**刻意保留重複**。

        去重的地方只有一個，在 `evaluator._grade_evidence`。同一個 collector 產生
        兩筆偵測時，這裡回傳兩個相同的來源名，evaluator 收斂成一個 → C2 而非 C3。
        若在這裡就去重，「兩筆」與「兩個來源」的差別會在到達判定之前消失。
        """
        found: list[str] = []
        for event_id in event_ids:
            record = self.records.by_id(event_id)
            if record is None:
                continue
            found.append(telemetry_source(record.get("labels") or {}))
        return tuple(found)

    def _telemetry_detail(
        self, execution: ActionExecution, expected_sources: tuple[str, ...]
    ) -> tuple[tuple[str, ...], tuple[tuple[str, bool], ...]]:
        """動作時間窗內、屬於這個動作的 raw 遙測行，**以及**每個來源個別
        有沒有命中（#126：Purple Console 畫面二的 Telemetry 欄輸入）。

        兩者是同一次 per-source 查詢的副產品，不為了第二個回傳值多打一次
        遙測後端 —— 那會讓 raw 遙測的呼叫量無故加倍。

        歸屬判準是 marker 字串，不是時間窗本身：兩個時間重疊的動作若只用窗過濾
        會互相偷對方的遙測，於是「看得到卻沒偵測到」和「根本沒看到」的分界線
        變成看誰先跑。

        `BackendUnavailable` 不在此攔截 —— 後端故障必須讓整次評分失敗，
        不能悄悄變成「這個動作沒有 raw 遙測」。
        """
        refs: list[str] = []
        by_source: list[tuple[str, bool]] = []
        for source in expected_sources:
            lines = self.telemetry.fetch_context(
                EvidenceQuery(
                    source=source,
                    start=execution.executed_at,
                    end=execution.window_end,
                )
            )
            matched = [line for line in lines if execution.marker in line.line]
            refs.extend(f"{line.source}@{line.timestamp.isoformat()}" for line in matched)
            by_source.append((source, bool(matched)))
        return tuple(refs), tuple(by_source)
