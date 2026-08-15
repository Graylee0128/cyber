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


def test_scenario_comes_from_the_running_exercise_not_the_rule_label(
    pg_connection,
    exercise_store,
    exercise_clock,
) -> None:
    """rule 的 scenario_id label 不決定歸屬，PostgreSQL 的 running exercise 才決定。

    一台 range 上多條規則同時掛著（`deploy/grafana/provisioning/alerting/rules.yaml`
    有五個不同的 scenario_id），跑 A scenario 時紅隊照樣可能觸發為 B 寫的規則。
    這種事件必須照常入庫並歸給當前這一場 —— 早期版本改成不符就丟棄，compose e2e
    當場證明那會把真實的 brute force 攻擊整條吞掉。
    """
    exercise_clock.current = datetime.now(timezone.utc) + timedelta(hours=1)
    events = CoreEventStore(pg_connection)
    records = AlertRecordStore(pg_connection)
    roster = (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),)

    exercise_store.start(scenario(), roster)
    exercise_clock.advance(timedelta(minutes=30))
    assert exercise_store.current() is None
    second = exercise_store.start(scenario("different-scenario"), roster)
    context = RunningExerciseLookup(pg_connection).require()

    # ALERT 的 label 寫死 scenario_id=sqli-01，當前跑的卻是 different-scenario。
    [event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, context.exercise_id),
        exercise_id=context.exercise_id,
        scenario_id=context.scenario_id,
    )

    assert context.exercise_id == second.exercise_id
    assert context.scenario_id == "different-scenario"
    core = events.by_id(event_id)[0]
    assert core["scenario_id"] == "different-scenario"  # 不是 label 的 sqli-01
    assert core["exercise_id"] == second.exercise_id


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
