"""#90 Phase 2：判定輸入只能來自真相來源，不能由呼叫端傳字面值。

這一組全用 fake store，但 fake 的是 **I/O**，不是判準：動作清單仍必須經過凍結
檢查、偵測仍必須靠 `action_id` 對應、raw 遙測仍必須靠 marker 過濾。
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.evaluation.action_registry import RegisteredAction, RegistryNotFrozen
from purple.evaluation.assembly import EvaluationAssembler
from purple.evaluation.evaluator import ActionState, EvidenceLevel
from purple.evidence.backends import BackendUnavailable, ContextLine, FakeBackend
from purple.registry.source_registry import ScenarioSource, evaluate_registry
from purple.store.executions import ActionExecution

NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
EXECUTED = NOW - timedelta(seconds=60)
WINDOW_END = EXECUTED + timedelta(seconds=90)
MARKER = "-- purple:abc action:a-hit"


class FakeRegistryStore:
    def __init__(self, actions, *, frozen=True):
        self._actions = actions
        self._frozen = frozen

    def get(self, exercise_id):
        class Snapshot:
            scenario_id = "sqli-01"

        return Snapshot()

    def frozen_actions(self, exercise_id):
        if not self._frozen:
            raise RegistryNotFrozen("coverage denominator requires a frozen registry")
        return self._actions


class FakeEvents:
    def __init__(self, detections, volume=0):
        self._detections = detections
        self._volume = volume

    def detections_by_action(self, exercise_id):
        return self._detections

    def alert_volume(self, exercise_id):
        return self._volume


class FakeRecords:
    def __init__(self, mapping):
        self._mapping = mapping

    def by_id(self, event_id):
        return self._mapping.get(event_id)


class FakeExecutions:
    def __init__(self, mapping):
        self._mapping = mapping

    def for_exercise(self, exercise_id):
        return self._mapping


class BrokenBackend:
    def fetch_context(self, query):
        raise BackendUnavailable("Loki 連不上")


def registry_for(scenario_id, *, falco=NOW, alloy=NOW):
    return evaluate_registry(
        scenario_id,
        [ScenarioSource("falco"), ScenarioSource("alloy")],
        {"falco": falco, "alloy": alloy},
        NOW,
    )


def telemetry_with_marker(marker=MARKER):
    return FakeBackend(
        lines=(
            ContextLine(
                timestamp=EXECUTED + timedelta(seconds=1),
                line=f"GET /login {marker}",
                source="falco",
            ),
            ContextLine(
                timestamp=EXECUTED + timedelta(seconds=2),
                line="unrelated traffic",
                source="falco",
            ),
        )
    )


def assemble(
    actions,
    *,
    detections=None,
    records=None,
    executions=None,
    volume=0,
    frozen=True,
    telemetry=None,
    source_registry=registry_for,
):
    return EvaluationAssembler(
        registry=FakeRegistryStore(actions, frozen=frozen),
        events=FakeEvents(detections or {}, volume),
        records=FakeRecords(records or {}),
        executions=FakeExecutions(executions or {}),
        source_registry=source_registry,
        telemetry=telemetry if telemetry is not None else telemetry_with_marker(),
    )


def execution(action_id, marker=MARKER):
    return ActionExecution("ex-1", action_id, EXECUTED, WINDOW_END, marker)


class TestDenominatorComesFromTheFrozenRegistry:
    """#21 §3.2 陷阱①：分母不得由現場事件反推。"""

    def test_unfrozen_registry_refuses_to_score(self):
        with pytest.raises(RegistryNotFrozen):
            assemble((), frozen=False).build("ex-1")

    def test_action_list_is_the_registry_snapshot_not_the_evidence(self):
        service = assemble(
            (
                RegisteredAction("a-hit", "T1190", "sqli"),
                RegisteredAction("a-idle", "T1059", "exec"),
            ),
            detections={"a-ghost": [{"event_id": "evt-x", "lifecycle": "firing"}]},
            executions={"a-hit": execution("a-hit")},
        ).build("ex-1")
        # 證據裡有一個不在 registry 上的 a-ghost —— 它不得出現在結果裡。
        assert [action for action, _ in service.actions] == ["a-hit", "a-idle"]
        assert {result.action_id for result in service.evaluate()} == {"a-hit", "a-idle"}


class TestCorrelationIsActionIdOnly:
    def test_detection_without_action_id_is_never_credited(self):
        """沒帶關聯鍵的偵測事件不會被分進任何一組，於是這個動作仍是 miss。"""
        result = assemble(
            (RegisteredAction("a-hit", "T1190", "sqli"),),
            detections={},  # store 依 `action_id IS NOT NULL` 過濾後就是空的
            executions={"a-hit": execution("a-hit")},
        ).build("ex-1").evaluate()[0]
        assert result.state is ActionState.MISS
        assert result.gap == "detection_gap"

    def test_same_technique_actions_do_not_share_evidence(self):
        service = assemble(
            (
                RegisteredAction("first", "T1190", "sqli"),
                RegisteredAction("second", "T1190", "sqli again"),
            ),
            detections={"second": [{"event_id": "evt-2", "lifecycle": "firing"}]},
            records={"evt-2": {"labels": {"source": "falco"}}},
            executions={
                "first": execution("first", "-- purple:1 action:first"),
                "second": execution("second", "-- purple:2 action:second"),
            },
        ).build("ex-1")
        first, second = service.evaluate()
        assert first.state is ActionState.MISS
        assert second.state is ActionState.HIT
        assert second.event_ids == ("evt-2",)


class TestEvidenceGradingUsesRealSources:
    def test_two_independent_collectors_reach_c3(self):
        result = assemble(
            (RegisteredAction("a-hit", "T1190", "sqli"),),
            detections={
                "a-hit": [
                    {"event_id": "evt-1", "lifecycle": "firing"},
                    {"event_id": "evt-2", "lifecycle": "firing"},
                ]
            },
            records={
                "evt-1": {"labels": {"source": "falco"}},
                "evt-2": {"labels": {"source": "alloy"}},
            },
            executions={"a-hit": execution("a-hit")},
        ).build("ex-1").evaluate()[0]
        assert result.level is EvidenceLevel.C3

    def test_one_collector_twice_stays_c2(self):
        result = assemble(
            (RegisteredAction("a-hit", "T1190", "sqli"),),
            detections={
                "a-hit": [
                    {"event_id": "evt-1", "lifecycle": "firing"},
                    {"event_id": "evt-2", "lifecycle": "firing"},
                ]
            },
            records={
                "evt-1": {"labels": {"source": "falco"}},
                "evt-2": {"labels": {"source": "falco"}},
            },
            executions={"a-hit": execution("a-hit")},
        ).build("ex-1").evaluate()[0]
        assert result.level is EvidenceLevel.C2


class TestGapClassificationUsesRealTelemetry:
    def test_marker_present_in_raw_telemetry_is_a_detection_gap(self):
        result = assemble(
            (RegisteredAction("a-miss", "T1190", "sqli"),),
            executions={"a-miss": execution("a-miss", MARKER)},
        ).build("ex-1").evaluate()[0]
        assert result.state is ActionState.MISS
        assert result.gap == "detection_gap"
        # #126：per-source marks 走同一次組裝（FakeBackend 不依 query.source
        # 過濾，兩個 expected source 拿到同一批行，marker 命中 → 都是 present）。
        marks = {mark.source_id: mark for mark in result.source_marks}
        assert set(marks) == {"falco", "alloy"}
        assert all(mark.event_present for mark in marks.values())

    def test_marker_absent_from_raw_telemetry_is_a_visibility_gap(self):
        result = assemble(
            (RegisteredAction("a-miss", "T1190", "sqli"),),
            executions={"a-miss": execution("a-miss", "-- purple:other action:a-miss")},
        ).build("ex-1").evaluate()[0]
        assert result.gap == "visibility_gap"
        marks = {mark.source_id: mark for mark in result.source_marks}
        assert set(marks) == {"falco", "alloy"}
        assert not any(mark.event_present for mark in marks.values())

    def test_stale_source_during_the_window_is_unknown_not_a_gap(self):
        stale = lambda scenario_id: registry_for(  # noqa: E731
            scenario_id, falco=NOW - timedelta(minutes=10), alloy=NOW - timedelta(minutes=10)
        )
        result = assemble(
            (RegisteredAction("a-blind", "T1190", "sqli"),),
            executions={"a-blind": execution("a-blind")},
            source_registry=stale,
        ).build("ex-1").evaluate()[0]
        assert result.state is ActionState.UNKNOWN
        assert result.gap is None


class TestFailuresAreNotDowngradedIntoBlueTeamGaps:
    def test_telemetry_backend_outage_propagates(self):
        """後端故障不得被寫成「藍隊沒看到」。"""
        with pytest.raises(BackendUnavailable):
            assemble(
                (RegisteredAction("a-miss", "T1190", "sqli"),),
                executions={"a-miss": execution("a-miss")},
                telemetry=BrokenBackend(),
            ).build("ex-1")

    def test_missing_telemetry_backend_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="telemetry"):
            EvaluationAssembler(
                registry=FakeRegistryStore(()),
                events=FakeEvents({}),
                records=FakeRecords({}),
                executions=FakeExecutions({}),
                source_registry=registry_for,
                telemetry=None,
            )

    def test_no_execution_record_is_not_executed_not_miss(self):
        result = assemble(
            (RegisteredAction("a-idle", "T1190", "sqli"),),
            executions={},
        ).build("ex-1").evaluate()[0]
        assert result.state is ActionState.NOT_EXECUTED


class TestMetricsComeFromTheStores:
    def test_alert_volume_and_excluded_counts_are_measured(self):
        metrics = assemble(
            (
                RegisteredAction("a-hit", "T1190", "sqli"),
                RegisteredAction("a-miss", "T1059", "exec"),
                RegisteredAction("a-idle", "T1005", "read"),
            ),
            detections={"a-hit": [{"event_id": "evt-1", "lifecycle": "firing"}]},
            records={"evt-1": {"labels": {"source": "falco"}}},
            executions={
                "a-hit": execution("a-hit"),
                "a-miss": execution("a-miss", "-- purple:zz action:a-miss"),
            },
            volume=42,
        ).build("ex-1").metrics()
        assert metrics.alert_volume == 42
        assert metrics.excluded_counts == {"unknown": 0, "not_executed": 1}
        assert metrics.action_coverage == pytest.approx(0.5)
