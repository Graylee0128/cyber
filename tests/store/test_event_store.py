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
    "action_id": None,
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

    def test_append_reports_whether_it_was_a_new_insert(self, store):
        """呼叫端（ingest_alert）靠這個值決定要不要 enqueue response 命令 ——
        repeat_interval 重送的 webhook 不該讓同一次攻擊被重複封鎖（#17）。"""
        assert store.append(FIRING) is True
        assert store.append(FIRING) is False
        assert store.append(RESOLVED) is True


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


class TestActionCorrelation:
    """#90 Phase 1-2：`action_id` 是唯一的 Action↔Evidence 關聯鍵。"""

    CORRELATED = {**FIRING, "event_id": "evt-correlated", "action_id": "a-1"}
    ORPHAN = {**FIRING, "event_id": "evt-orphan", "action_id": None}
    RESPONSE = {
        **FIRING,
        "event_id": "evt-response",
        "event_type": "response.executed",
        "visibility": "blue",
        "action_id": "a-1",
    }

    def test_events_are_grouped_by_action_id(self, store):
        store.append(self.CORRELATED)
        assert store.detections_by_action("ex-001") == {
            "a-1": [self.CORRELATED]
        }

    def test_events_without_an_action_id_are_not_grouped_anywhere(self, store):
        """沒帶關聯鍵的事件不得被拿去對應任何註冊動作。"""
        store.append(self.ORPHAN)
        assert store.detections_by_action("ex-001") == {}

    def test_response_events_are_not_counted_as_detections(self, store):
        """反應不是偵測。把 response.executed 算進去會讓 coverage 自己餵自己。"""
        store.append(self.RESPONSE)
        assert store.detections_by_action("ex-001") == {}

    def test_another_exercise_does_not_leak_in(self, store):
        store.append({**self.CORRELATED, "exercise_id": "ex-other"})
        assert store.detections_by_action("ex-001") == {}

    def test_alert_volume_counts_firing_only(self, store):
        """firing 與 resolved 共用 event_id；兩筆都數會讓每個告警被算兩次。"""
        store.append(FIRING)
        store.append(RESOLVED)
        assert store.alert_volume("ex-001") == 1


class TestLatencyCorrelation:
    """#90 Phase 4：latency 的三個時間終點各走各的 join key。"""

    FIRING = {**FIRING, "event_id": "evt-fire", "action_id": "a-1"}
    RESOLVED = {
        **FIRING,
        "event_id": "evt-fire",
        "action_id": "a-1",
        "lifecycle": "resolved",
        "observed_at": "2026-08-08T14:40:00+08:00",
    }
    RESPONSE = {
        **FIRING,
        "event_id": "evt-resp",
        "event_type": "response.executed",
        "visibility": "blue",
        "action_id": None,
        "observed_at": "2026-08-08T14:32:00+08:00",
        "target": {"service": "vulnerable-app", "attack_event_id": "evt-fire"},
    }

    def test_firing_is_keyed_by_action_id(self, store):
        store.append(self.FIRING)
        firings = store.firings_by_action("ex-001")
        assert firings["a-1"]["event_id"] == "evt-fire"

    def test_response_is_keyed_by_the_attack_event_it_answered(self, store):
        store.append(self.FIRING)
        store.append(self.RESPONSE)
        responses = store.responses_by_attack_event("ex-001")
        # 以攻擊 event_id 對接，不是 response 自己的 event_id
        assert "evt-fire" in responses
        assert "evt-resp" not in responses

    def test_resolution_shares_the_firing_event_id(self, store):
        store.append(self.FIRING)
        store.append(self.RESOLVED)
        resolutions = store.resolutions_by_event("ex-001")
        assert "evt-fire" in resolutions


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
