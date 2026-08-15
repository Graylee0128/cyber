"""Alert lifecycle —— firing 與 resolved 共用 event_id（票 05），對真 PG 驗。"""

from purple.metrics.containment import containment_duration
from purple.receiver import ingest_alert
from purple.store.events import CoreEventStore
from purple.store.alerts import AlertRecordStore
from purple.store.fingerprints import FingerprintIndex

import pytest


def _alert(status: str, observed_at: str) -> dict:
    return {
        "status": status,
        "fingerprint": "fp-sqli-0001",  # firing 與 resolved 同一個 fingerprint
        "startsAt": observed_at,
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
        "annotations": {"query": "q", "threshold": "> 4"},
        "values": {"A": 12},
    }


@pytest.fixture
def wired(pg_connection):
    return (
        CoreEventStore(pg_connection),
        AlertRecordStore(pg_connection),
        FingerprintIndex(pg_connection, "ex-current"),
    )


def _ingest(webhook, events, records, fps):
    return ingest_alert(
        webhook,
        events=events,
        records=records,
        fingerprints=fps,
        exercise_id="ex-current",
        scenario_id="sqli-01",
    )


class TestSharedEventId:
    def test_resolved_shares_event_id_with_firing(self, wired):
        events, records, fps = wired
        [firing_id] = _ingest(
            {"status": "firing", "alerts": [_alert("firing", "2026-08-08T14:30:00+08:00")]},
            events, records, fps,
        )
        [resolved_id] = _ingest(
            {"status": "resolved", "alerts": [_alert("resolved", "2026-08-08T14:35:00+08:00")]},
            events, records, fps,
        )
        assert resolved_id == firing_id

        both = events.by_id(firing_id)
        assert [e["lifecycle"] for e in both] == ["firing", "resolved"]

    def test_containment_duration_computable_from_the_pair(self, wired):
        events, records, fps = wired
        _ingest({"status": "firing", "alerts": [_alert("firing", "2026-08-08T14:30:00+08:00")]},
                events, records, fps)
        [event_id] = _ingest(
            {"status": "resolved", "alerts": [_alert("resolved", "2026-08-08T14:35:00+08:00")]},
            events, records, fps,
        )
        from datetime import timedelta
        assert containment_duration(events.by_id(event_id)) == timedelta(minutes=5)


class TestNoInformationlessRows:
    def test_pending_produces_no_event(self, wired):
        """Battleboard timeline 不會出現『快要偵測到了』這種無資訊量的行。"""
        events, records, fps = wired
        ids = _ingest(
            {"status": "pending", "alerts": [_alert("pending", "2026-08-08T14:30:00+08:00")]},
            events, records, fps,
        )
        assert ids == []
        assert events.count() == 0
