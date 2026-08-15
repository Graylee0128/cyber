"""Receiver pure core —— 純函數，秒級，不觸 PG（票 03「core 可獨立單元測試」）。"""

from purple.harness.schema import SchemaViolation
from purple.receiver.core import (
    build_alert_record,
    build_core_event,
    lifecycle_of,
    mint_event_id,
)

import pytest

ALERT = {
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
        "visibility": "instructor",  # rule 想覆寫 —— 必須被忽略
    },
    "annotations": {"query": '{app="x"} |= "OR 1=1"', "threshold": "> 4 / 1m"},
    "values": {"A": 12},
}


class TestBuildCoreEvent:
    def test_exercise_id_is_injected_instead_of_trusting_the_alert_label(self):
        core = build_core_event(
            ALERT,
            "evt-1",
            "firing",
            exercise_id="ex-current-running",
        )
        assert core["exercise_id"] == "ex-current-running"

    def test_visibility_comes_from_event_type_not_label(self):
        core = build_core_event(ALERT, "evt-1", "firing", exercise_id="ex-current")
        assert core["visibility"] == "public"  # attack.detected → public

    def test_core_event_has_no_backend_fields(self):
        core = build_core_event(ALERT, "evt-1", "firing", exercise_id="ex-current")
        blob = repr(core).lower()
        for word in ("loki", "logql", "promql", "backend", "evidence_ref", "query", "threshold"):
            assert word not in blob, f"{word} leaked into Core Event"

    def test_event_id_is_carried_through(self):
        assert build_core_event(
            ALERT, "evt-xyz", "firing", exercise_id="ex-current"
        )["event_id"] == "evt-xyz"

    def test_target_is_the_service(self):
        assert build_core_event(
            ALERT, "evt-1", "firing", exercise_id="ex-current"
        )["target"] == {"service": "vulnerable-app"}

    def test_response_source_ip_is_carried_from_governed_alert_label(self):
        with_source = {
            **ALERT,
            "labels": {**ALERT["labels"], "source_ip": "10.167.30.11"},
        }

        assert build_core_event(with_source, "evt-1", "firing", exercise_id="ex-current")["target"] == {
            "service": "vulnerable-app",
            "source_ip": "10.167.30.11",
        }

    def test_invalid_event_is_rejected_before_leaving_core(self):
        bad = {**ALERT, "labels": {**ALERT["labels"], "event_type": "attack.maybe"}}
        with pytest.raises(SchemaViolation):
            build_core_event(bad, "evt-1", "firing", exercise_id="ex-current")


class TestLifecycle:
    def test_firing_maps_to_firing(self):
        assert lifecycle_of({"status": "firing"}) == "firing"

    def test_resolved_maps_to_resolved(self):
        assert lifecycle_of({"status": "resolved"}) == "resolved"

    def test_pending_produces_no_lifecycle(self):
        assert lifecycle_of({"status": "pending"}) is None

    def test_missing_status_produces_no_lifecycle(self):
        assert lifecycle_of({}) is None


class TestBuildAlertRecord:
    def test_backend_lives_here(self):
        assert build_alert_record(ALERT, "evt-1")["backend"] == "loki"

    def test_carries_rule_query_threshold(self):
        rec = build_alert_record(ALERT, "evt-1")
        assert rec["grafana_rule"] == "SQLInjectionBurst"
        assert "OR 1=1" in rec["query"]
        assert rec["threshold"] == "> 4 / 1m"

    def test_shares_the_minted_event_id(self):
        assert build_alert_record(ALERT, "evt-shared")["event_id"] == "evt-shared"


class TestMintEventId:
    def test_ids_are_unique(self):
        assert mint_event_id() != mint_event_id()

    def test_ids_are_prefixed(self):
        assert mint_event_id().startswith("evt-")
