"""Receiver shell —— 對真 PG 驗證編排順序與外送。"""

from purple.receiver import ingest_alert
from purple.receiver.adapters import RecordingAdapter
from purple.response.direct_block import RecordingBlocker
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore

import pytest

FIRING = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-1",
            "startsAt": "2026-08-08T14:30:00+08:00",
            "labels": {
                "alertname": "SQLInjectionBurst",
                "event_type": "attack.detected",
                "technique": "T1190",
                "team": "red",
                "severity": "high",
                "scenario_id": "sqli-01",
                "exercise_id": "ex-001",
                "service": "vulnerable-app",
            },
            "annotations": {"query": '{app="x"} |= "OR 1=1"', "threshold": "> 4 / 1m"},
            "values": {"A": 12},
        }
    ],
}

PENDING = {
    "status": "pending",
    "alerts": [{**FIRING["alerts"][0], "status": "pending"}],
}


@pytest.fixture
def wired(pg_connection):
    return (
        CoreEventStore(pg_connection),
        AlertRecordStore(pg_connection),
        RecordingAdapter(),
        RecordingBlocker(),
    )


class TestIngest:
    def test_one_alert_produces_one_core_event(self, wired):
        events, records, adapter, blocker = wired
        ids = ingest_alert(FIRING, events=events, records=records, adapter=adapter, blocker=blocker)
        assert len(ids) == 1
        assert events.count() == 1

    def test_alert_record_written_before_core_event(self, wired):
        """順序不可顛倒：Core Event 存在時，對應的 Alert Record 必已存在。"""
        events, records, adapter, blocker = wired
        [event_id] = ingest_alert(
            FIRING, events=events, records=records, adapter=adapter, blocker=blocker
        )
        assert records.exists(event_id)
        assert records.by_id(event_id)["backend"] == "loki"

    def test_event_id_is_shared_by_both_records(self, wired):
        events, records, adapter, blocker = wired
        [event_id] = ingest_alert(
            FIRING, events=events, records=records, adapter=adapter, blocker=blocker
        )
        assert events.by_id(event_id)[0]["event_id"] == event_id
        assert records.by_id(event_id)["event_id"] == event_id

    def test_downstream_adapter_receives_the_core_event(self, wired):
        events, records, adapter, blocker = wired
        ingest_alert(FIRING, events=events, records=records, adapter=adapter, blocker=blocker)
        assert len(adapter.delivered) == 1
        assert adapter.delivered[0]["scenario_id"] == "sqli-01"

    def test_attack_triggers_ipset_direct_write(self, wired):
        """票 03 的 expand：attack.detected 觸發 ipset 直寫（票 09 才 contract）。"""
        events, records, adapter, blocker = wired
        ingest_alert(FIRING, events=events, records=records, adapter=adapter, blocker=blocker)
        assert len(blocker.blocked) == 1

    def test_pending_produces_nothing(self, wired):
        events, records, adapter, blocker = wired
        ids = ingest_alert(PENDING, events=events, records=records, adapter=adapter, blocker=blocker)
        assert ids == []
        assert events.count() == 0
