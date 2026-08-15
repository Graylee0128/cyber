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
            # Grafana 的 fingerprint 只由 rule 的 label 集合決定，所以同一條規則
            # 在下一場會再次送出**一模一樣**的值。跨場次測試靠這個才成立。
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
    first_exercise_id = lookup.require_id()
    [first_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, first_exercise_id),
        exercise_id=first_exercise_id,
    )

    exercise_clock.advance(timedelta(minutes=30))
    assert exercise_store.current() is None
    second = exercise_store.start(scenario(), roster)
    second_exercise_id = lookup.require_id()
    [second_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, second_exercise_id),
        exercise_id=second_exercise_id,
    )

    first_core = events.by_id(first_event_id)[0]
    second_core = events.by_id(second_event_id)[0]
    assert first.exercise_id != second.exercise_id
    # 這條是 fingerprint 作用域的迴歸點：全域鍵時第二場會撈回第一場鑄的 id，
    # 兩個 event_id 相同，第二場的事件因此被靜默掛到已結束的場次上。
    assert first_event_id != second_event_id
    assert first_core["exercise_id"] == first.exercise_id
    assert second_core["exercise_id"] == second.exercise_id


def _resolved_of(alert: dict) -> dict:
    """同一個告警的 resolved：fingerprint 相同，status 換成 resolved。"""
    [firing] = alert["alerts"]
    return {"alerts": [{**firing, "status": "resolved", "endsAt": "2026-08-11T08:05:00+00:00"}]}


def test_resolved_arriving_after_rollover_still_pairs_with_its_firing(
    pg_connection,
    exercise_store,
    exercise_clock,
) -> None:
    """換場之後才到的 resolved 屬於 firing 那一場，不屬於當下這場（契約 §2.2）。

    Grafana 的 resolved 常常晚到，演練已經結束、下一場已經開始是正常情形。
    fingerprint 對映若只在當前場次裡找，這筆 resolved 會配不到 firing，
    鑄一個新 event_id 掛在新場次上 —— 舊場次的 firing 永遠沒有終點，
    `resolutions_by_event()` 依 exercise_id 過濾，含制時間靜默變成不可得。
    """
    exercise_clock.current = datetime.now(timezone.utc) + timedelta(hours=1)
    events = CoreEventStore(pg_connection)
    records = AlertRecordStore(pg_connection)
    lookup = RunningExerciseLookup(pg_connection)
    roster = (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),)

    first = exercise_store.start(scenario(), roster)
    first_exercise_id = lookup.require_id()
    [firing_event_id] = ingest_alert(
        ALERT,
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, first_exercise_id),
        exercise_id=first_exercise_id,
    )

    exercise_clock.advance(timedelta(minutes=30))
    second = exercise_store.start(scenario(), roster)
    second_exercise_id = lookup.require_id()
    [resolved_event_id] = ingest_alert(
        _resolved_of(ALERT),
        events=events,
        records=records,
        fingerprints=FingerprintIndex(pg_connection, second_exercise_id),
        exercise_id=second_exercise_id,
    )

    # §2.2：firing 與 resolved 共用同一個 event_id。
    assert resolved_event_id == firing_event_id

    # 而且歸在 firing 的那一場 —— 不是當下正在跑的第二場。
    resolved_core = [e for e in events.by_id(resolved_event_id) if e["lifecycle"] == "resolved"]
    assert len(resolved_core) == 1
    assert resolved_core[0]["exercise_id"] == first.exercise_id
    assert resolved_core[0]["exercise_id"] != second.exercise_id

    # 第一場查得到自己的終點；第二場不該把別場的 resolved 當成自己的。
    assert firing_event_id in events.resolutions_by_event(first.exercise_id)
    assert events.resolutions_by_event(second.exercise_id) == {}


def test_resolved_within_the_same_exercise_still_pairs(
    pg_connection,
    exercise_store,
    exercise_clock,
) -> None:
    """跨場次的回退不能弄壞常態：同一場內的 resolved 照樣配到同一個 event_id。"""
    exercise_clock.current = datetime.now(timezone.utc) + timedelta(hours=1)
    events = CoreEventStore(pg_connection)
    records = AlertRecordStore(pg_connection)
    lookup = RunningExerciseLookup(pg_connection)
    roster = (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),)

    exercise = exercise_store.start(scenario(), roster)
    exercise_id = lookup.require_id()
    index = FingerprintIndex(pg_connection, exercise_id)

    [firing_event_id] = ingest_alert(
        ALERT, events=events, records=records, fingerprints=index, exercise_id=exercise_id
    )
    [resolved_event_id] = ingest_alert(
        _resolved_of(ALERT),
        events=events,
        records=records,
        fingerprints=index,
        exercise_id=exercise_id,
    )

    assert resolved_event_id == firing_event_id
    assert firing_event_id in events.resolutions_by_event(exercise.exercise_id)


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
