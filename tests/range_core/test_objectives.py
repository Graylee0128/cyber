"""#33 WS5-3/4/5: PlayerLookup, ObjectiveStore, HintService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from range_core.exercises import ExerciseStore, PlayerRegistration
from range_core.objectives import (
    HintIndexOutOfRange,
    HintService,
    ObjectiveEvaluationMismatch,
    ObjectiveStore,
    PlayerLookup,
)
from range_core.scenarios import Scenario


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "range-chain-01",
            "name": "Range Chain",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {
                    "id": "gain_shell",
                    "evaluation": "telemetry",
                    "points": 200,
                    "telemetry_signal": {"action_id": "gain-shell"},
                },
                {"id": "capture_flag", "evaluation": "submission", "points": 500},
            ],
            "hints": [
                {
                    "objective_id": "capture_flag",
                    "text": "Inspect local application data.",
                    "penalty_percent": 50,
                },
                {
                    "objective_id": "capture_flag",
                    "text": "Check /var/lib/purplescope.",
                    "penalty_percent": 80,
                },
            ],
            "targets": [{"host": "target-01", "surfaces": ["web", "local-shell"]}],
            "expected_sources": ["falco", "alloy"],
            "attack_chain": [
                {"id": "gain-shell", "technique": "T1059", "description": "Gain a shell."},
            ],
            "reset_scope": "environment",
        }
    )


@pytest.fixture
def running_exercise(exercise_store: ExerciseStore):
    return exercise_store.start(
        scenario(),
        (
            PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),
            PlayerRegistration(player_id="red-bob", source_ip="10.167.30.12"),
        ),
    )


class TestPlayerLookup:
    def test_known_source_ip_resolves_to_its_player(self, pg_connection, running_exercise):
        lookup = PlayerLookup(pg_connection)

        assert lookup.player_for(running_exercise.exercise_id, "10.167.30.11") == "red-alice"
        assert lookup.player_for(running_exercise.exercise_id, "10.167.30.12") == "red-bob"

    def test_unknown_source_ip_resolves_to_none(self, pg_connection, running_exercise):
        lookup = PlayerLookup(pg_connection)

        assert lookup.player_for(running_exercise.exercise_id, "10.167.30.13") is None
        assert lookup.player_for(running_exercise.exercise_id, "10.167.20.10") is None


class TestObjectiveStore:
    def test_submission_completion_has_no_evidence_event_id(self, pg_connection, running_exercise):
        store = ObjectiveStore(pg_connection)
        objective = scenario().objectives[1]  # capture_flag, submission

        inserted = store.complete_by_submission(
            running_exercise.exercise_id, "red-alice", "capture_flag", objective=objective
        )

        assert inserted is True
        completions = store.for_exercise(running_exercise.exercise_id)
        assert len(completions) == 1
        assert completions[0].evaluation == "submission"
        assert completions[0].evidence_event_id is None

    def test_telemetry_completion_carries_its_triggering_event_id(
        self, pg_connection, running_exercise
    ):
        store = ObjectiveStore(pg_connection)
        objective = scenario().objectives[0]  # gain_shell, telemetry

        store.complete_by_telemetry(
            running_exercise.exercise_id, "red-alice", "gain_shell", "evt-abc123", objective=objective
        )

        completions = store.for_exercise(running_exercise.exercise_id)
        assert completions[0].evaluation == "telemetry"
        assert completions[0].evidence_event_id == "evt-abc123"

    def test_submission_path_rejects_a_telemetry_objective(self, pg_connection, running_exercise):
        store = ObjectiveStore(pg_connection)
        telemetry_objective = scenario().objectives[0]

        with pytest.raises(ObjectiveEvaluationMismatch):
            store.complete_by_submission(
                running_exercise.exercise_id, "red-alice", "gain_shell", objective=telemetry_objective
            )

    def test_telemetry_path_rejects_a_submission_objective(self, pg_connection, running_exercise):
        store = ObjectiveStore(pg_connection)
        submission_objective = scenario().objectives[1]

        with pytest.raises(ObjectiveEvaluationMismatch):
            store.complete_by_telemetry(
                running_exercise.exercise_id,
                "red-alice",
                "capture_flag",
                "evt-abc123",
                objective=submission_objective,
            )

    def test_repeat_completion_does_not_duplicate_or_move_completed_at(
        self, pg_connection, running_exercise
    ):
        store = ObjectiveStore(pg_connection)
        objective = scenario().objectives[1]

        first = store.complete_by_submission(
            running_exercise.exercise_id, "red-alice", "capture_flag", objective=objective
        )
        second = store.complete_by_submission(
            running_exercise.exercise_id, "red-alice", "capture_flag", objective=objective
        )

        assert first is True
        assert second is False
        completions = store.for_exercise(running_exercise.exercise_id)
        assert len(completions) == 1

    def test_two_players_completing_the_same_objective_both_get_a_row(
        self, pg_connection, running_exercise
    ):
        store = ObjectiveStore(pg_connection)
        objective = scenario().objectives[1]

        store.complete_by_submission(
            running_exercise.exercise_id, "red-alice", "capture_flag", objective=objective
        )
        store.complete_by_submission(
            running_exercise.exercise_id, "red-bob", "capture_flag", objective=objective
        )

        completions = store.for_exercise(running_exercise.exercise_id)
        assert {c.player_id for c in completions} == {"red-alice", "red-bob"}


class TestHintService:
    def test_penalties_for_lists_index_and_penalty_without_text(self, pg_connection, running_exercise):
        service = HintService(pg_connection)

        penalties = service.penalties_for(scenario(), "capture_flag")

        assert penalties == (
            {"index": 0, "penalty_percent": 50},
            {"index": 1, "penalty_percent": 80},
        )
        assert all("text" not in p for p in penalties)

    def test_request_returns_text_and_records_usage(self, pg_connection, running_exercise):
        service = HintService(pg_connection)

        text = service.request(
            scenario(), running_exercise.exercise_id, "red-alice", "capture_flag", 0
        )

        assert text == "Inspect local application data."
        usages = service.for_exercise(running_exercise.exercise_id)
        assert len(usages) == 1
        assert usages[0].hint_index == 0

    def test_repeat_request_for_same_index_does_not_double_record(
        self, pg_connection, running_exercise
    ):
        service = HintService(pg_connection)

        service.request(scenario(), running_exercise.exercise_id, "red-alice", "capture_flag", 0)
        service.request(scenario(), running_exercise.exercise_id, "red-alice", "capture_flag", 0)

        usages = service.for_exercise(running_exercise.exercise_id)
        assert len(usages) == 1

    def test_index_is_scoped_per_objective(self, pg_connection):
        two_objective_scenario = Scenario.model_validate(
            {
                "id": "two-hinted",
                "name": "Two Hinted",
                "difficulty": "easy",
                "duration": "30m",
                "objectives": [
                    {"id": "obj-a", "evaluation": "submission", "points": 100},
                    {"id": "obj-b", "evaluation": "submission", "points": 100},
                ],
                "hints": [
                    {"objective_id": "obj-a", "text": "hint a0", "penalty_percent": 10},
                    {"objective_id": "obj-b", "text": "hint b0", "penalty_percent": 10},
                ],
                "targets": [{"host": "target-01", "surfaces": ["web"]}],
                "expected_sources": ["alloy"],
                "attack_chain": [
                    {"id": "a-1", "technique": "T1190", "description": "exploit"}
                ],
                "reset_scope": "exercise",
            }
        )
        exercise = ExerciseStore(pg_connection).start(
            two_objective_scenario,
            (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),),
        )
        service = HintService(pg_connection)

        text_a = service.request(two_objective_scenario, exercise.exercise_id, "red-alice", "obj-a", 0)
        text_b = service.request(two_objective_scenario, exercise.exercise_id, "red-alice", "obj-b", 0)

        assert text_a == "hint a0"
        assert text_b == "hint b0"
        usages = service.for_exercise(exercise.exercise_id)
        assert len(usages) == 2

    def test_out_of_range_index_is_rejected(self, pg_connection, running_exercise):
        service = HintService(pg_connection)

        with pytest.raises(HintIndexOutOfRange):
            service.request(
                scenario(), running_exercise.exercise_id, "red-alice", "capture_flag", 99
            )


def test_hint_usage_rows_cascade_away_on_reset(pg_connection, running_exercise):
    HintService(pg_connection).request(
        scenario(), running_exercise.exercise_id, "red-alice", "capture_flag", 0
    )

    ExerciseStore(pg_connection).reset_current()

    remaining = pg_connection.execute(
        "SELECT count(*) FROM exercise_hint_usages WHERE exercise_id = %s",
        (running_exercise.exercise_id,),
    ).fetchone()
    assert remaining[0] == 0
