from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from purple.receiver import ingest_alert
from purple.receiver.exercise_context import RunningExerciseLookup
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore
from purple.store.fingerprints import FingerprintIndex
from range_core.exercises import PlayerRegistration
from range_core.scenarios import Scenario


ALERT = {
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-same-grafana-rule",
            "startsAt": "2026-08-11T08:00:00+00:00",
            "labels": {
                "alertname": "SQLInjectionBurst",
                "event_type": "attack.detected",
                "technique": "T1190",
                "team": "red",
                "severity": "high",
                "scenario_id": "sqli-01",
                "exercise_id": "ex-stale-label-must-be-ignored",
                "service": "vulnerable-app",
            },
            "annotations": {},
            "values": {},
        }
    ]
}


def scenario(scenario_id: str = "sqli-01") -> Scenario:
    return Scenario.model_validate(
        {
            "id": scenario_id,
            "name": "SQL Injection",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "attack_chain": [
                {
                    "id": "exploit-web",
                    "technique": "T1190",
                    "description": "Exploit the target.",
                }
            ],
            "reset_scope": "exercise",
        }
    )


def test_two_sequential_exercises_emit_core_events_with_distinct_exercise_ids(
    pg_connection,
    exercise_store,
    exercise_clock,
) -> None:
    exercise_clock.current = datetime.now(timezone.utc) + timedelta(hours=1)
    events = CoreEventStore(pg_connection)
    records = AlertRecordStore(pg_connection)
    lookup = RunningExerciseLookup(pg_connection)
    roster = (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),)

    first = exercise_store.start(scenario(), roster)
    first_context = lookup.require()
    [first_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, first_context.exercise_id),
        exercise_id=first_context.exercise_id,
        scenario_id=first_context.scenario_id,
    )

    exercise_clock.advance(timedelta(minutes=30))
    assert exercise_store.current() is None
    second = exercise_store.start(scenario(), roster)
    second_context = lookup.require()
    [second_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, second_context.exercise_id),
        exercise_id=second_context.exercise_id,
        scenario_id=second_context.scenario_id,
    )

    first_core = events.by_id(first_event_id)[0]
    second_core = events.by_id(second_event_id)[0]
    assert first.exercise_id != second.exercise_id
    assert first_event_id != second_event_id
    assert first_core["exercise_id"] == first.exercise_id
    assert second_core["exercise_id"] == second.exercise_id


def test_delayed_alert_from_an_old_scenario_is_not_relabelled_to_the_new_one(
    pg_connection,
    exercise_store,
    exercise_clock,
) -> None:
    exercise_clock.current = datetime.now(timezone.utc) + timedelta(hours=1)
    events = CoreEventStore(pg_connection)
    records = AlertRecordStore(pg_connection)
    roster = (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),)

    exercise_store.start(scenario(), roster)
    exercise_clock.advance(timedelta(minutes=30))
    assert exercise_store.current() is None
    second = exercise_store.start(scenario("different-scenario"), roster)
    context = RunningExerciseLookup(pg_connection).require()

    emitted = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, context.exercise_id),
        exercise_id=context.exercise_id,
        scenario_id=context.scenario_id,
    )

    assert context.exercise_id == second.exercise_id
    assert context.scenario_id == "different-scenario"
    assert emitted == []
    assert events.count() == 0


def test_grafana_rules_do_not_claim_a_static_exercise_identity() -> None:
    rules = (
        Path(__file__).resolve().parents[2]
        / "deploy"
        / "grafana"
        / "provisioning"
        / "alerting"
        / "rules.yaml"
    ).read_text(encoding="utf-8")

    assert "exercise_id:" not in rules
