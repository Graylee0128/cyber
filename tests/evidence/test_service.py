"""Evidence API 服務（票 #14）—— render 與 status 對應。

render_bundle 與 handle_evidence 的判定邏輯用 stub resolver 測（不需 PG 資料）；
另有一條對真 PG + FakeBackend 的整合，證明端到端在 zone 內成立（非全棧）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.evidence import (
    BackendUnavailable,
    Caller,
    ContextLine,
    EvidenceBundle,
    EvidenceNotFound,
    EvidenceResolver,
    FakeBackend,
    UnknownCaller,
    handle_evidence,
    render_bundle,
)
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 8, 8, 14, 30, tzinfo=TZ)


def _bundle(lines=()) -> EvidenceBundle:
    return EvidenceBundle(
        event_id="evt-1",
        rule="SQLInjectionBurst",
        window_start=T0 - timedelta(minutes=5),
        window_end=T0 + timedelta(minutes=5),
        lines=tuple(lines),
    )


class TestRenderBundle:
    def test_shape_has_no_backend_or_query(self):
        body = render_bundle(_bundle([ContextLine(T0, "req [blue]", "blue", "vulnerable-app")]))
        assert set(body) == {"event_id", "rule", "window_start", "window_end", "line_count", "lines"}
        blob = repr(body).lower()
        assert "backend" not in blob and "logql" not in blob and "promql" not in blob

    def test_lines_serialized_with_visibility(self):
        body = render_bundle(_bundle([ContextLine(T0, "x", "purple", "vulnerable-app")]))
        assert body["line_count"] == 1
        assert body["lines"][0]["visibility"] == "purple"
        assert body["lines"][0]["timestamp"].startswith("2026-08-08")


class _StubResolver:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def resolve(self, event_id, *, caller):
        if self._error:
            raise self._error
        return self._result


class TestStatusMapping:
    def test_missing_identity_is_400(self):
        status, body = handle_evidence("evt-1", None, _StubResolver(result=_bundle()))
        assert status == 400
        assert "X-Purple-Identity" in body["error"]

    def test_success_is_200_with_body(self):
        status, body = handle_evidence("evt-1", "blue", _StubResolver(result=_bundle()))
        assert status == 200
        assert body["event_id"] == "evt-1"

    def test_unknown_caller_is_403(self):
        status, _ = handle_evidence("evt-1", "ceo", _StubResolver(error=UnknownCaller("nope")))
        assert status == 403

    def test_not_found_is_404(self):
        status, _ = handle_evidence("evt-x", "blue", _StubResolver(error=EvidenceNotFound("no")))
        assert status == 404

    def test_backend_unavailable_is_503(self):
        status, _ = handle_evidence("evt-1", "blue", _StubResolver(error=BackendUnavailable("no loki")))
        assert status == 503

    def test_unexpected_error_is_500_without_leaking_details(self):
        """DB 之類非預期錯誤 → 500，但不得把內部訊息回給呼叫者。"""
        secret = "connection to db at /secret/path failed"
        status, body = handle_evidence("evt-1", "blue", _StubResolver(error=RuntimeError(secret)))
        assert status == 500
        assert secret not in body["error"]


# --- 對真 PG + FakeBackend 的整合（非全棧）--------------------------------

EVENT_ID = "evt-service-0001"
CORE_EVENT = {
    "event_id": EVENT_ID, "exercise_id": "ex-001", "scenario_id": "sqli-01",
    "event_type": "attack.detected", "lifecycle": "firing", "severity": "high",
    "source": "grafana", "team": "red", "technique": "T1190",
    "target": {"service": "vulnerable-app"}, "observed_at": T0.isoformat(),
    "visibility": "public",
}
ALERT_RECORD = {
    "event_id": EVENT_ID, "grafana_rule": "SQLInjectionBurst",
    "query": '{app="vulnerable-app"}', "threshold": "> 0", "fired_values": [],
    "labels": {"service": "vulnerable-app"}, "backend": "loki",
}
CONTEXT = (
    ContextLine(T0.replace(minute=29), "before [public]", "public", "vulnerable-app"),
    ContextLine(T0.replace(minute=30), "hit [blue]", "blue", "vulnerable-app"),
    ContextLine(T0.replace(minute=31), "after [purple]", "purple", "vulnerable-app"),
)


@pytest.fixture
def resolver(pg_connection):
    records = AlertRecordStore(pg_connection)
    events = CoreEventStore(pg_connection)
    records.write(ALERT_RECORD)
    events.append(CORE_EVENT)
    return EvidenceResolver(records=records, events=events, backend=FakeBackend(CONTEXT))


def test_end_to_end_returns_context_window_filtered_by_caller(resolver):
    # blue 看得到 public+blue，看不到 purple
    status, body = handle_evidence(EVENT_ID, "blue", resolver)
    assert status == 200
    vis = {line["visibility"] for line in body["lines"]}
    assert vis == {"public", "blue"}
    assert body["line_count"] >= 2  # 上下文窗，不是單行

    # red 只看得到 public
    _, red_body = handle_evidence(EVENT_ID, "red", resolver)
    assert {line["visibility"] for line in red_body["lines"]} == {"public"}
