"""#33 WS5-5: read-time score derivation. Pure — no DB, no PG needed."""

from __future__ import annotations

from datetime import datetime, timezone

from range_core.objectives import HintUsage, ObjectiveCompletion
from range_core.scenarios import Scenario
from range_core.scoring import derive_scores

NOW = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)


def scenario_with_hints() -> Scenario:
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
                {"objective_id": "capture_flag", "text": "hint 0", "penalty_percent": 10},
                {"objective_id": "capture_flag", "text": "hint 1", "penalty_percent": 80},
            ],
            "targets": [{"host": "target-01", "surfaces": ["web", "local-shell"]}],
            "expected_sources": ["falco", "alloy"],
            "attack_chain": [
                {"id": "gain-shell", "technique": "T1059", "description": "Gain a shell."}
            ],
            "reset_scope": "environment",
        }
    )


def completion(player_id, objective_id, evaluation="submission", evidence_event_id=None, at=NOW):
    return ObjectiveCompletion(
        exercise_id="ex-1",
        player_id=player_id,
        objective_id=objective_id,
        completed_at=at,
        evaluation=evaluation,
        evidence_event_id=evidence_event_id,
    )


def hint_usage(player_id, objective_id, hint_index):
    return HintUsage(
        exercise_id="ex-1",
        player_id=player_id,
        objective_id=objective_id,
        hint_index=hint_index,
        used_at=NOW,
    )


def test_score_with_no_hints_equals_sum_of_completed_points():
    scenario = scenario_with_hints()
    completions = (
        completion("red-alice", "gain_shell", evaluation="telemetry", evidence_event_id="evt-1"),
        completion("red-alice", "capture_flag"),
    )

    board = derive_scores(scenario, completions, ())

    assert board.red_total == 700
    assert board.players[0].total == 700


def test_hint_on_one_objective_only_discounts_that_objective():
    scenario = scenario_with_hints()
    completions = (
        completion("red-alice", "gain_shell", evaluation="telemetry", evidence_event_id="evt-1"),
        completion("red-alice", "capture_flag"),
    )
    hints = (hint_usage("red-alice", "capture_flag", 0),)  # 10% off capture_flag only

    board = derive_scores(scenario, completions, hints)

    by_id = {o.objective_id: o for o in board.players[0].objectives}
    assert by_id["gain_shell"].awarded == 200  # untouched
    assert by_id["capture_flag"].awarded == 450  # 500 * 0.9


def test_stacking_two_hints_on_one_objective_takes_the_max_penalty():
    scenario = scenario_with_hints()
    completions = (completion("red-alice", "capture_flag"),)
    hints = (
        hint_usage("red-alice", "capture_flag", 0),  # 10%
        hint_usage("red-alice", "capture_flag", 1),  # 80%
    )

    board = derive_scores(scenario, completions, hints)

    assert board.players[0].objectives[0].awarded == 100  # 500 * 0.2, not 500 * (1 - 0.9)
    assert board.players[0].objectives[0].penalty_percent == 80


def test_two_players_scores_are_independent():
    scenario = scenario_with_hints()
    completions = (
        completion("red-alice", "capture_flag"),
        completion("red-bob", "capture_flag"),
    )
    hints = (hint_usage("red-alice", "capture_flag", 1),)  # only alice used a hint

    board = derive_scores(scenario, completions, hints)

    by_player = {p.player_id: p for p in board.players}
    assert by_player["red-alice"].total == 100  # 500 * 0.2
    assert by_player["red-bob"].total == 500  # untouched


def test_score_is_recomputable_from_records_alone():
    """Drift-proofing: the same inputs must always yield the same output —
    there is nowhere for a stored value to diverge from because there is no
    stored value."""
    scenario = scenario_with_hints()
    completions = (
        completion("red-alice", "gain_shell", evaluation="telemetry", evidence_event_id="evt-1"),
        completion("red-alice", "capture_flag"),
    )
    hints = (hint_usage("red-alice", "capture_flag", 0),)

    first = derive_scores(scenario, completions, hints)
    second = derive_scores(scenario, completions, hints)

    assert first.as_dict() == second.as_dict()


def test_every_awarded_point_traces_to_a_listed_objective_and_hint():
    scenario = scenario_with_hints()
    completions = (
        completion("red-alice", "gain_shell", evaluation="telemetry", evidence_event_id="evt-42"),
        completion("red-alice", "capture_flag"),
    )
    hints = (hint_usage("red-alice", "capture_flag", 0),)

    board = derive_scores(scenario, completions, hints)

    payload = board.as_dict()
    player = payload["red"]["players"][0]
    objective_ids = {o["objective_id"] for o in player["objectives"]}
    assert objective_ids == {"gain_shell", "capture_flag"}
    gain_shell = next(o for o in player["objectives"] if o["objective_id"] == "gain_shell")
    assert gain_shell["evidence_event_id"] == "evt-42"
    capture_flag = next(o for o in player["objectives"] if o["objective_id"] == "capture_flag")
    assert capture_flag["hints_used"] == [0]


def test_response_shape_has_no_blue_key():
    """ADR 0002 決策②: v1 ships red-only. Non-zero-sum holds vacuously
    because there is no Blue score for a Red action to affect."""
    board = derive_scores(scenario_with_hints(), (), ())

    assert "blue" not in board.as_dict()
    assert set(board.as_dict().keys()) == {"red"}
