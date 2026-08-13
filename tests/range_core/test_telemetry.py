"""#33 WS5-3: telemetry-driven objective completion.

Pure-matcher tests need no DB. `TestSyncTelemetryObjectives` needs `core_events`
(a purple/Z-MGMT table), so it goes through `purple.store.events.CoreEventStore`
directly — that's fine for a *test*, only `src/range_core/**` itself must
never import purple (enforced by `test_boundary.py`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from purple.store.events import CoreEventStore

from range_core.exercises import ExerciseStore, PlayerRegistration
from range_core.objectives import ObjectiveStore
from range_core.scenarios import Scenario
from range_core.telemetry import SkippedEvent, match_telemetry_objectives, sync_telemetry_objectives

NOW = datetime(2026, 8, 13, 6, 0, tzinfo=timezone.utc)


def two_kali_scenario() -> Scenario:
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
            "targets": [{"host": "target-01", "surfaces": ["web", "local-shell"]}],
            "expected_sources": ["falco", "alloy"],
            "attack_chain": [
                {"id": "gain-shell", "technique": "T1059", "description": "Gain a shell."},
            ],
            "reset_scope": "environment",
        }
    )


def core_event(
    *,
    action_id: str | None,
    source_ip: str | None,
    event_type: str = "attack.detected",
    lifecycle: str = "firing",
) -> dict:
    target = {"service": "range-target"}
    if source_ip is not None:
        target["source_ip"] = source_ip
    return {
        "event_id": "evt-" + action_id if action_id else "evt-none",
        "exercise_id": "ex-1",
        "scenario_id": "range-chain-01",
        "event_type": event_type,
        "lifecycle": lifecycle,
        "severity": "high",
        "source": "grafana",
        "team": "red",
        "technique": "T1059",
        "target": target,
        "observed_at": NOW.isoformat(),
        "visibility": "red",
        "action_id": action_id,
    }


class TestMatchTelemetryObjectives:
    def test_matching_action_id_and_roster_ip_completes_the_objective(self):
        scenario = two_kali_scenario()
        roster = {"10.167.30.11": "red-alice"}
        events = (
            ("evt-1", core_event(action_id="gain-shell", source_ip="10.167.30.11")),
        )

        result = match_telemetry_objectives(scenario, roster, events)

        assert result.completions == (("red-alice", "gain_shell", "evt-1"),)
        assert result.skipped == ()

    def test_attribution_is_per_player_not_team_wide(self):
        scenario = two_kali_scenario()
        roster = {"10.167.30.11": "red-alice", "10.167.30.12": "red-bob"}
        events = (
            ("evt-1", core_event(action_id="gain-shell", source_ip="10.167.30.11")),
            ("evt-2", core_event(action_id="gain-shell", source_ip="10.167.30.12")),
        )

        result = match_telemetry_objectives(scenario, roster, events)

        assert set(result.completions) == {
            ("red-alice", "gain_shell", "evt-1"),
            ("red-bob", "gain_shell", "evt-2"),
        }

    def test_submission_objective_is_never_completed_this_way(self):
        """Guards against an implicit-id-equality regression: a submission
        objective whose id equals an attack_chain action id must not
        complete just because a matching Core Event arrives."""
        scenario = Scenario.model_validate(
            {
                "id": "trap-01",
                "name": "Trap",
                "difficulty": "easy",
                "duration": "30m",
                "objectives": [
                    {"id": "gain-shell", "evaluation": "submission", "points": 500}
                ],
                "targets": [{"host": "target-01", "surfaces": ["web"]}],
                "expected_sources": ["falco"],
                "attack_chain": [
                    {"id": "gain-shell", "technique": "T1059", "description": "Gain a shell."}
                ],
                "reset_scope": "environment",
            }
        )
        roster = {"10.167.30.11": "red-alice"}
        events = (("evt-1", core_event(action_id="gain-shell", source_ip="10.167.30.11")),)

        result = match_telemetry_objectives(scenario, roster, events)

        assert result.completions == ()

    def test_event_with_no_source_ip_completes_nothing_and_reports_reason(self):
        scenario = two_kali_scenario()
        roster = {"10.167.30.11": "red-alice"}
        events = (("evt-1", core_event(action_id="gain-shell", source_ip=None)),)

        result = match_telemetry_objectives(scenario, roster, events)

        assert result.completions == ()
        assert result.skipped == (SkippedEvent(event_id="evt-1", reason="no_source_ip"),)

    def test_non_roster_source_ip_completes_nothing(self):
        scenario = two_kali_scenario()
        roster = {"10.167.30.11": "red-alice"}
        events = (
            ("evt-1", core_event(action_id="gain-shell", source_ip="10.167.20.10")),
        )

        result = match_telemetry_objectives(scenario, roster, events)

        assert result.completions == ()
        assert result.skipped[0].reason == "unknown_source"

    def test_response_events_are_excluded_even_with_a_matching_action_id(self):
        """A response.executed event's source_ip is the blocked attacker's
        address; counting it would let a Blue containment action award
        Red points, breaking WS1 §1.1's non-zero-sum rule."""
        scenario = two_kali_scenario()
        roster = {"10.167.30.11": "red-alice"}
        events = (
            (
                "evt-1",
                core_event(
                    action_id="gain-shell",
                    source_ip="10.167.30.11",
                    event_type="response.executed",
                ),
            ),
        )

        result = match_telemetry_objectives(scenario, roster, events)

        assert result.completions == ()


class TestSyncTelemetryObjectives:
    def _running(self, pg_connection):
        scenario = two_kali_scenario()
        exercise = ExerciseStore(pg_connection).start(
            scenario,
            (
                PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),
                PlayerRegistration(player_id="red-bob", source_ip="10.167.30.12"),
            ),
        )
        return scenario, exercise

    def test_matching_firing_event_completes_the_objective_with_evidence(self, pg_connection):
        scenario, exercise = self._running(pg_connection)
        event = core_event(action_id="gain-shell", source_ip="10.167.30.11")
        event["exercise_id"] = exercise.exercise_id
        CoreEventStore(pg_connection).append(event)

        result = sync_telemetry_objectives(pg_connection, scenario, exercise.exercise_id)

        assert result.completions == (("red-alice", "gain_shell", event["event_id"]),)
        completions = ObjectiveStore(pg_connection).for_exercise(exercise.exercise_id)
        assert completions[0].evidence_event_id == event["event_id"]

    def test_idempotent_across_repeated_syncs(self, pg_connection):
        scenario, exercise = self._running(pg_connection)
        event = core_event(action_id="gain-shell", source_ip="10.167.30.11")
        event["exercise_id"] = exercise.exercise_id
        CoreEventStore(pg_connection).append(event)

        sync_telemetry_objectives(pg_connection, scenario, exercise.exercise_id)
        sync_telemetry_objectives(pg_connection, scenario, exercise.exercise_id)
        sync_telemetry_objectives(pg_connection, scenario, exercise.exercise_id)

        completions = ObjectiveStore(pg_connection).for_exercise(exercise.exercise_id)
        assert len(completions) == 1
        first_completed_at = completions[0].completed_at

        sync_telemetry_objectives(pg_connection, scenario, exercise.exercise_id)
        completions_again = ObjectiveStore(pg_connection).for_exercise(exercise.exercise_id)
        assert completions_again[0].completed_at == first_completed_at

    def test_resolved_duplicate_of_a_firing_event_creates_no_second_row(self, pg_connection):
        scenario, exercise = self._running(pg_connection)
        firing = core_event(action_id="gain-shell", source_ip="10.167.30.11", lifecycle="firing")
        firing["exercise_id"] = exercise.exercise_id
        resolved = dict(firing)
        resolved["lifecycle"] = "resolved"
        CoreEventStore(pg_connection).append(firing)
        CoreEventStore(pg_connection).append(resolved)

        sync_telemetry_objectives(pg_connection, scenario, exercise.exercise_id)

        completions = ObjectiveStore(pg_connection).for_exercise(exercise.exercise_id)
        assert len(completions) == 1
