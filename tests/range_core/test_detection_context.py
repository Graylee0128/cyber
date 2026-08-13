from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from purple.receiver import ingest_alert
from purple.receiver.exercise_context import RunningExerciseLookup
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore
from range_core.exercises import PlayerRegistration
from range_core.scenarios import Scenario


ALERT = {
    "alerts": [
        {
            "status": "firing",
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


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "sqli-01",
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
    [first_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        exercise_id=lookup.require_id(),
    )

    exercise_clock.advance(timedelta(minutes=30))
    assert exercise_store.current() is None
    second = exercise_store.start(scenario(), roster)
    [second_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        exercise_id=lookup.require_id(),
    )

    first_core = events.by_id(first_event_id)[0]
    second_core = events.by_id(second_event_id)[0]
    assert first.exercise_id != second.exercise_id
    assert first_core["exercise_id"] == first.exercise_id
    assert second_core["exercise_id"] == second.exercise_id


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
