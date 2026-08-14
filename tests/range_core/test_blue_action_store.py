"""`BlueActionStore` — the I/O shell around the pure `blue_actions` rules
(#36 Phase 2). Needs real PG: the one-shot guarantee under concurrent
submissions is enforced by `blue_actions_one_judgement_idx`, a database
constraint, not by anything a fake connection could stand in for.

Every test needs a real `exercises` row (`exercise_blue_actions.exercise_id`
has an FK to it) — same setup `test_objectives.py` uses for the tables it
FKs against.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from purple.evaluation.action_registry import ActionRegistryStore, RegisteredAction
from purple.receiver.whitelist import default_whitelist
from purple.store.events import CoreEventStore
from purple.store.executions import ActionExecutionStore

from range_core.blue_action_store import (
    AlreadyJudged,
    BlueActionStore,
    StoredDispatchOutcome,
    StoredExecutionEvidence,
    UnknownEvent,
)
from range_core.blue_actions import BlueActionRejected
from range_core.exercises import ExerciseStore, PlayerRegistration
from range_core.scenarios import Scenario


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "scn-1",
            "name": "Blue Store Fixture",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "attack_chain": [
                {"id": "exploit-web", "technique": "T1190", "description": "Exploit."}
            ],
            "reset_scope": "exercise",
        }
    )


@pytest.fixture
def running_exercise(exercise_store: ExerciseStore):
    return exercise_store.start(
        scenario(),
        (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),),
    )


def _core_event(
    exercise_id: str,
    event_id: str,
    *,
    technique: str = "T1190",
    lifecycle: str = "firing",
    action_id: str | None = None,
    event_type: str = "attack.detected",
) -> dict:
    return {
        "event_id": event_id,
        "exercise_id": exercise_id,
        "scenario_id": "scn-1",
        "event_type": event_type,
        "lifecycle": lifecycle,
        "severity": "high",
        "source": "grafana",
        "team": "red",
        "technique": technique,
        "target": {"service": "range-target"},
        "observed_at": "2026-08-13T06:00:00+00:00",
        "visibility": "red",
        "action_id": action_id,
    }


@pytest.fixture
def store(pg_connection):
    class FixedClock:
        def now(self):
            return datetime(2026, 8, 13, 6, 0, 30, tzinfo=timezone.utc)

    return BlueActionStore(pg_connection, clock=FixedClock())


class TestRecordRequiresARealEvent:
    def test_unknown_event_id_is_rejected(self, store, running_exercise):
        with pytest.raises(UnknownEvent):
            store.record(running_exercise.exercise_id, "acknowledge", "evt-does-not-exist")

    def test_known_event_id_is_accepted(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        recorded = store.record(running_exercise.exercise_id, "acknowledge", "evt-1")
        assert recorded.event_id == "evt-1"

    def test_non_detection_event_cannot_be_used_as_a_reaction_origin(
        self, store, pg_connection, running_exercise
    ):
        CoreEventStore(pg_connection).append(
            _core_event(
                running_exercise.exercise_id,
                "evt-response",
                event_type="response.executed",
            )
        )

        with pytest.raises(UnknownEvent):
            store.record(running_exercise.exercise_id, "acknowledge", "evt-response")


class TestShapeValidationIsRejectedBeforeAnyWrite:
    def test_unknown_action_is_rejected(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        with pytest.raises(BlueActionRejected):
            store.record(running_exercise.exercise_id, "escalate", "evt-1")

    def test_classify_without_technique_is_rejected(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        with pytest.raises(BlueActionRejected):
            store.record(running_exercise.exercise_id, "classify", "evt-1")

    def test_rejected_submission_leaves_no_row(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        with pytest.raises(BlueActionRejected):
            store.record(running_exercise.exercise_id, "classify", "evt-1")
        assert store.for_exercise(running_exercise.exercise_id).actions == ()


class TestOneShotIsEnforcedByTheDatabase:
    def test_second_classify_is_rejected(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "classify", "evt-1", "T1190")
        with pytest.raises(AlreadyJudged):
            store.record(running_exercise.exercise_id, "classify", "evt-1", "T1110")

    def test_dismiss_after_classify_is_rejected(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "classify", "evt-1", "T1190")
        with pytest.raises(AlreadyJudged):
            store.record(running_exercise.exercise_id, "dismiss", "evt-1")

    def test_repeated_acknowledge_is_not_a_one_shot_violation(self, store, pg_connection, running_exercise):
        """重按封鎖鈕不該報錯 —— 一次定生死只限判讀類動作。"""
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "acknowledge", "evt-1")
        store.record(running_exercise.exercise_id, "acknowledge", "evt-1")
        assert len(store.for_exercise(running_exercise.exercise_id).actions) == 2

    def test_judgement_on_a_different_event_is_unaffected(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-2"))
        store.record(running_exercise.exercise_id, "classify", "evt-1", "T1190")
        store.record(running_exercise.exercise_id, "classify", "evt-2", "T1190")  # does not raise
        assert len(store.for_exercise(running_exercise.exercise_id).actions) == 2


class TestForExercise:
    def test_round_trips_through_the_pure_domain_type(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "acknowledge", "evt-1")

        log = store.for_exercise(running_exercise.exercise_id)

        assert log.actions[0].event_id == "evt-1"
        assert log.actions[0].action.value == "acknowledge"

    def test_only_this_exercise_is_returned(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "acknowledge", "evt-1")

        assert store.for_exercise("some-other-exercise").actions == ()

    def test_action_audit_survives_exercise_reset(
        self, store, pg_connection, exercise_store, running_exercise
    ):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "acknowledge", "evt-1")

        exercise_store.reset_current()

        assert len(store.for_exercise(running_exercise.exercise_id).actions) == 1


class TestTechniqueByEvent:
    def test_reads_the_real_unmasked_technique(self, store, pg_connection, running_exercise):
        """這裡讀的是落地值，不經過 disclosure 的欄位遮蔽 —— 評分本來就
        需要真值，遮蔽只作用在推給玩家的那份投影。"""
        CoreEventStore(pg_connection).append(
            _core_event(running_exercise.exercise_id, "evt-1", technique="T1110")
        )

        assert store.technique_by_event(running_exercise.exercise_id) == {"evt-1": "T1110"}

    def test_resolved_duplicate_does_not_produce_a_second_entry(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(
            _core_event(running_exercise.exercise_id, "evt-1", lifecycle="firing")
        )
        CoreEventStore(pg_connection).append(
            _core_event(running_exercise.exercise_id, "evt-1", lifecycle="resolved")
        )

        assert store.technique_by_event(running_exercise.exercise_id) == {"evt-1": "T1190"}


class TestObservedAtByEvent:
    def test_uses_the_firing_row_not_the_resolved_one(self, store, pg_connection, running_exercise):
        firing = _core_event(running_exercise.exercise_id, "evt-1", lifecycle="firing")
        firing["observed_at"] = "2026-08-13T06:00:00+00:00"
        resolved = _core_event(running_exercise.exercise_id, "evt-1", lifecycle="resolved")
        resolved["observed_at"] = "2026-08-13T06:05:00+00:00"
        CoreEventStore(pg_connection).append(firing)
        CoreEventStore(pg_connection).append(resolved)

        result = store.observed_at_by_event(running_exercise.exercise_id)

        assert result["evt-1"].isoformat().startswith("2026-08-13T06:00:00")


class TestStoredExecutionEvidence:
    def test_missing_independent_execution_is_false(
        self, pg_connection, running_exercise
    ):
        CoreEventStore(pg_connection).append(
            _core_event(running_exercise.exercise_id, "evt-false-positive")
        )

        evidence = StoredExecutionEvidence(pg_connection, running_exercise.exercise_id)

        assert evidence.has_evidence("evt-false-positive") is False

    def test_registered_execution_linked_by_action_id_is_true(
        self, pg_connection, running_exercise
    ):
        exercise_id = running_exercise.exercise_id
        ActionRegistryStore(pg_connection, default_whitelist()).seed(
            exercise_id,
            "scn-1",
            [RegisteredAction("exploit-web", "T1190", "Exploit the web target")],
        )
        ActionExecutionStore(pg_connection).record(
            exercise_id,
            "exploit-web",
            datetime(2026, 8, 13, 5, 59, tzinfo=timezone.utc),
            "-- purple:test action:exploit-web",
        )
        CoreEventStore(pg_connection).append(
            _core_event(
                exercise_id,
                "evt-real-attack",
                action_id="exploit-web",
            )
        )

        evidence = StoredExecutionEvidence(pg_connection, exercise_id)

        assert evidence.has_evidence("evt-real-attack") is True


class TestDispatchStatus:
    """#51／WS3 spec §5.2：contain 落地後的派送結果回填。"""

    def test_freshly_recorded_contain_has_no_dispatch_outcome_yet(
        self, store, pg_connection, running_exercise
    ):
        """落地跟派送是兩個先後步驟——落地那一刻還不知道派送結果，
        `dispatched()` 在真的寫入之前該是保守的 False，不是提前樂觀。"""
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        store.record(running_exercise.exercise_id, "contain", "evt-1")

        outcome = StoredDispatchOutcome(pg_connection, running_exercise.exercise_id)
        assert outcome.dispatched("evt-1") is False

    def test_set_dispatched_makes_it_visible(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        recorded = store.record(running_exercise.exercise_id, "contain", "evt-1")

        store.set_dispatch_status(
            running_exercise.exercise_id, "evt-1", recorded.submitted_at, "dispatched"
        )

        outcome = StoredDispatchOutcome(pg_connection, running_exercise.exercise_id)
        assert outcome.dispatched("evt-1") is True

    def test_set_failed_stays_visible_as_not_dispatched(
        self, store, pg_connection, running_exercise
    ):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        recorded = store.record(running_exercise.exercise_id, "contain", "evt-1")

        store.set_dispatch_status(
            running_exercise.exercise_id, "evt-1", recorded.submitted_at, "failed"
        )

        outcome = StoredDispatchOutcome(pg_connection, running_exercise.exercise_id)
        assert outcome.dispatched("evt-1") is False

    def test_unknown_status_value_is_rejected(self, store, pg_connection, running_exercise):
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        recorded = store.record(running_exercise.exercise_id, "contain", "evt-1")

        with pytest.raises(ValueError):
            store.set_dispatch_status(
                running_exercise.exercise_id, "evt-1", recorded.submitted_at, "maybe"
            )

    def test_only_the_first_contain_attempt_is_what_scoring_reads(
        self, store, pg_connection, running_exercise
    ):
        """contain 可以重複送（WS3 spec §4.2 只鎖判讀類動作）；派送狀態要看
        的是第一筆——跟 `blue_scoring` 用 `contain_seconds` 只認第一次一致，
        兩邊的「第一筆」定義必須是同一個，分數才對得起來。"""
        CoreEventStore(pg_connection).append(_core_event(running_exercise.exercise_id, "evt-1"))
        first = store.record(running_exercise.exercise_id, "contain", "evt-1")
        store.set_dispatch_status(
            running_exercise.exercise_id, "evt-1", first.submitted_at, "dispatched"
        )

        outcome = StoredDispatchOutcome(pg_connection, running_exercise.exercise_id)
        assert outcome.dispatched("evt-1") is True

    def test_no_contain_at_all_is_not_dispatched(self, pg_connection, running_exercise):
        outcome = StoredDispatchOutcome(pg_connection, running_exercise.exercise_id)
        assert outcome.dispatched("evt-never-happened") is False
