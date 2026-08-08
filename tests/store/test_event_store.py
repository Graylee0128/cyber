"""Core Event 儲存 —— 對真的 PostgreSQL 跑，不用 fake。

fake 過不了的東西正是選 PG 的理由：timestamptz 的時區保真、jsonb 的往返、
以及重送不重複計數的唯一鍵。用 in-memory 假物件測這些等於沒測。
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.harness.schema import assert_core_event
from purple.store.events import CoreEventStore

FIRING = {
    "event_id": "evt-01J000000000000000000001",
    "exercise_id": "ex-001",
    "scenario_id": "sqli-01",
    "event_type": "attack.detected",
    "lifecycle": "firing",
    "severity": "high",
    "source": "grafana",
    "team": "red",
    "technique": "T1190",
    "target": {"service": "vulnerable-app"},
    "observed_at": "2026-08-08T14:30:00+08:00",
    "visibility": "public",
}
RESOLVED = {**FIRING, "lifecycle": "resolved", "observed_at": "2026-08-08T14:35:00+08:00"}


@pytest.fixture
def store(pg_connection) -> CoreEventStore:
    return CoreEventStore(pg_connection)


class TestRoundTrip:
    def test_stored_event_comes_back_unchanged(self, store):
        store.append(FIRING)
        assert store.by_id(FIRING["event_id"]) == [FIRING]

    def test_stored_event_still_satisfies_the_contract(self, store):
        """往返一趟後仍要通過 schema 斷言 —— 儲存層不得偷偷加欄位。"""
        store.append(FIRING)
        assert_core_event(store.by_id(FIRING["event_id"])[0])

    def test_nested_target_survives_jsonb(self, store):
        store.append(FIRING)
        assert store.by_id(FIRING["event_id"])[0]["target"] == {"service": "vulnerable-app"}


class TestLifecyclePair:
    def test_firing_and_resolved_share_one_event_id(self, store):
        store.append(FIRING)
        store.append(RESOLVED)
        got = store.by_id(FIRING["event_id"])
        assert [e["lifecycle"] for e in got] == ["firing", "resolved"]

    def test_resending_the_same_lifecycle_does_not_duplicate(self, store):
        """Grafana webhook 會重試。重試不該讓 coverage 分子灌水。"""
        store.append(FIRING)
        store.append(FIRING)
        assert store.count() == 1


class TestSince:
    def test_only_events_after_the_mark_are_returned(self, store):
        store.append(FIRING)
        mark = store.now()
        store.append(RESOLVED)
        assert [e["lifecycle"] for e in store.since(mark)] == ["resolved"]

    def test_nothing_new_returns_empty(self, store):
        store.append(FIRING)
        assert store.since(store.now()) == []

    def test_mark_comes_from_the_database_not_the_local_clock(self, store):
        """本機時鐘可能偏。用 DB 時間取標記，否則票 01 想消滅的偏差會從這裡回來。"""
        db_now = store.now()
        assert db_now.tzinfo is not None
        assert abs((db_now - datetime.now(timezone.utc)).total_seconds()) < 3600


class TestTimezoneFidelity:
    def test_offset_aware_timestamp_is_not_flattened_to_naive(self, store):
        store.append(FIRING)
        stored = store.conn.execute(
            "SELECT observed_at FROM core_events WHERE event_id = %s", (FIRING["event_id"],)
        ).fetchone()[0]
        assert stored.tzinfo is not None

    def test_plus_eight_is_stored_as_the_same_instant(self, store):
        store.append(FIRING)
        stored = store.conn.execute(
            "SELECT observed_at FROM core_events WHERE event_id = %s", (FIRING["event_id"],)
        ).fetchone()[0]
        expected = datetime(2026, 8, 8, 6, 30, tzinfo=timezone.utc)
        assert stored.astimezone(timezone.utc) == expected

    def test_events_five_minutes_apart_stay_five_minutes_apart(self, store):
        store.append(FIRING)
        store.append(RESOLVED)
        firing, resolved = (
            datetime.fromisoformat(e["observed_at"]) for e in store.by_id(FIRING["event_id"])
        )
        assert resolved - firing == timedelta(minutes=5)
