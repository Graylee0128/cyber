"""票 04 的四條紅燈 —— 欄位在離開 receiver 前就被治理。

pure core（build_core_event）不觸 PG；shell 的拒收記錄用 caplog 驗。
"""

from purple.harness.schema import VISIBILITY_BY_EVENT_TYPE, expected_visibility
from purple.receiver.core import build_alert_record, build_core_event
from purple.receiver.whitelist import TechniqueRejected

import logging
import pytest

BASE = {
    "status": "firing",
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
        "visibility": "instructor",
    },
    "annotations": {"query": "q", "threshold": "> 4"},
    "values": {"A": 12},
}


def _with_technique(tid: str) -> dict:
    return {**BASE, "labels": {**BASE["labels"], "technique": tid}}


class TestTechniqueOutsideWhitelistRejected:
    def test_pure_core_raises(self):
        with pytest.raises(TechniqueRejected, match="T9999"):
            build_core_event(_with_technique("T9999"), "evt-1", "firing")

    def test_whitelisted_passes(self):
        core = build_core_event(_with_technique("T1078"), "evt-1", "firing")
        assert core["technique"] == "T1078"


class TestRuleCannotOverrideVisibility:
    def test_label_visibility_is_ignored(self):
        # labels 想標 instructor，但 attack.detected 一律 public
        core = build_core_event(BASE, "evt-1", "firing")
        assert core["visibility"] == "public"

    def test_every_spec_event_type_has_a_visibility(self):
        for event_type in VISIBILITY_BY_EVENT_TYPE:
            assert expected_visibility(event_type) is not None
        assert expected_visibility("exercise.started") == "instructor"


class TestCoreEventContainsNoBackendFields:
    def test_no_backend_vocabulary(self):
        core = build_core_event(BASE, "evt-1", "firing")
        blob = repr(core).lower()
        for word in ("loki", "logql", "promql", "backend"):
            assert word not in blob

    def test_backend_lives_only_in_alert_record(self):
        assert build_alert_record(BASE, "evt-1")["backend"] == "loki"


class TestRejectionIsRecordedNotSilent:
    """『拒收並記錄，不得靜默通過』—— shell 對壞 technique 要留下 WARNING。"""

    def test_shell_logs_rejected_alert(self, pg_connection, caplog):
        from purple.receiver import ingest_alert
        from purple.receiver.adapters import RecordingAdapter
        from purple.response.queue import InMemoryCommandQueue
        from purple.store.alerts import AlertRecordStore
        from purple.store.events import CoreEventStore

        events = CoreEventStore(pg_connection)
        records = AlertRecordStore(pg_connection)
        webhook = {"status": "firing", "alerts": [_with_technique("T9999")]}

        with caplog.at_level(logging.WARNING, logger="purple.receiver"):
            ids = ingest_alert(
                webhook, events=events, records=records,
                adapter=RecordingAdapter(), response_queue=InMemoryCommandQueue(),
            )

        assert ids == []                    # 沒有 Core Event
        assert events.count() == 0
        assert any("T9999" in r.message for r in caplog.records)  # 但有記錄

    def test_rejected_technique_leaves_no_orphan_alert_record(self, pg_connection):
        from purple.receiver import ingest_alert
        from purple.store.alerts import AlertRecordStore
        from purple.store.events import CoreEventStore

        events = CoreEventStore(pg_connection)
        records = AlertRecordStore(pg_connection)
        webhook = {"status": "firing", "alerts": [_with_technique("T9999")]}

        ingest_alert(webhook, events=events, records=records)
        # 沒有 Core Event，也不該有孤兒 Alert Record
        assert (
            pg_connection.execute("SELECT count(*) FROM alert_records").fetchone()[0] == 0
        )
