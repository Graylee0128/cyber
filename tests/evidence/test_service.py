"""Evidence API 服務（票 #14／#52 B2）—— render、token→identity 換出、status 對應。

render_bundle 與 handle_evidence 的判定邏輯用 stub resolver 測（不需 PG 資料）；
另有一條對真 PG + FakeBackend 的整合，證明端到端在 zone 內成立（非全棧）。

B2：identity 由部署時注入的服務 token 換出，呼叫端無法自報（WS7 spec §2）。
`TestSelfReportProtection` 是驗收條件那條「拿掉檢查時測試必須變紅」的測試。
"""

import logging
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
    extract_token,
    handle_evidence,
    load_service_tokens,
    render_bundle,
    resolve_identity,
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
        self.last_caller = None  # 記錄 resolver 實際收到的 caller，供自報防護測試檢查

    def resolve(self, event_id, *, caller):
        self.last_caller = caller
        if self._error:
            raise self._error
        return self._result


TOKEN_MAP = {"blue-secret": "blue"}


class TestLoadServiceTokens:
    def test_builds_token_to_identity_map_from_env(self):
        env = {
            "PURPLE_EVIDENCE_TOKEN_BLUE": "blue-secret",
            "PURPLE_EVIDENCE_TOKEN_PURPLE": "purple-secret",
        }
        assert load_service_tokens(env) == {"blue-secret": "blue", "purple-secret": "purple"}

    def test_identity_without_env_var_has_no_token(self):
        tokens = load_service_tokens({"PURPLE_EVIDENCE_TOKEN_BLUE": "blue-secret"})
        assert "instructor" not in tokens.values()

    def test_empty_env_yields_empty_map_fail_closed(self):
        """沒設任何 token 變數 → 空表，不是「預設放行」。"""
        assert load_service_tokens({}) == {}


class TestExtractToken:
    def test_reads_bearer_token(self):
        assert extract_token({"Authorization": "Bearer blue-secret"}) == "blue-secret"

    def test_missing_header_is_none(self):
        assert extract_token({}) is None

    def test_non_bearer_scheme_is_ignored(self):
        assert extract_token({"Authorization": "Basic blue-secret"}) is None


class TestSelfReportProtection:
    """B2 acceptance criteria：呼叫者宣稱自己是 purple／instructor 必須被拒絕或
    降回其 token 對應的 clearance；把檢查拿掉時，下面每條測試都要變紅。"""

    def test_forged_identity_string_does_not_resolve_as_token(self):
        """把想冒充的身分字串直接當 token 送 —— 查無此 token。"""
        for forged in ("purple", "instructor", "blue"):
            assert resolve_identity(forged, TOKEN_MAP) is None

    def test_legacy_identity_header_has_no_effect(self):
        """舊的 X-Purple-Identity 頭欄位完全不影響身分判定 —— 只認 Authorization token。"""
        headers = {"Authorization": "Bearer blue-secret", "X-Purple-Identity": "instructor"}
        assert extract_token(headers) == "blue-secret"

    def test_end_to_end_grants_only_the_tokens_own_clearance(self):
        """即使同時塞了自報 header 宣稱 instructor，resolver 收到的 caller 仍是 token 對應的 blue。"""
        stub = _StubResolver(result=_bundle())
        headers = {"Authorization": "Bearer blue-secret", "X-Purple-Identity": "instructor"}
        token = extract_token(headers)

        status, _ = handle_evidence("evt-1", token, stub, TOKEN_MAP)

        assert status == 200
        assert stub.last_caller == Caller("blue")


class TestStatusMapping:
    def test_missing_token_is_400(self):
        status, body = handle_evidence("evt-1", None, _StubResolver(result=_bundle()), TOKEN_MAP)
        assert status == 400
        assert "Authorization" in body["error"]

    def test_success_is_200_with_body(self):
        status, body = handle_evidence("evt-1", "blue-secret", _StubResolver(result=_bundle()), TOKEN_MAP)
        assert status == 200
        assert body["event_id"] == "evt-1"

    def test_unknown_token_is_403(self):
        status, _ = handle_evidence("evt-1", "not-a-real-token", _StubResolver(), TOKEN_MAP)
        assert status == 403

    def test_resolver_unknown_caller_still_maps_to_403(self):
        """defense-in-depth：萬一 token_map 混入 CALLER_CLEARANCE 沒有的身分，resolver 那層還是擋得住。"""
        status, _ = handle_evidence(
            "evt-1", "weird-secret", _StubResolver(error=UnknownCaller("nope")), {"weird-secret": "ceo"}
        )
        assert status == 403

    def test_not_found_is_404(self):
        status, _ = handle_evidence(
            "evt-x", "blue-secret", _StubResolver(error=EvidenceNotFound("no")), TOKEN_MAP
        )
        assert status == 404

    def test_backend_unavailable_is_503(self):
        status, _ = handle_evidence(
            "evt-1", "blue-secret", _StubResolver(error=BackendUnavailable("no loki")), TOKEN_MAP
        )
        assert status == 503

    def test_unexpected_error_is_500_without_leaking_details(self):
        """DB 之類非預期錯誤 → 500，但不得把內部訊息回給呼叫者。"""
        secret = "connection to db at /secret/path failed"
        status, body = handle_evidence(
            "evt-1", "blue-secret", _StubResolver(error=RuntimeError(secret)), TOKEN_MAP
        )
        assert status == 500
        assert secret not in body["error"]


class TestTokenNeverLeaked:
    def test_unknown_token_not_echoed_in_error_body(self):
        status, body = handle_evidence("evt-1", "totally-wrong-token", _StubResolver(), TOKEN_MAP)
        assert status == 403
        assert "totally-wrong-token" not in str(body)

    def test_unexpected_error_does_not_log_token(self, caplog):
        with caplog.at_level(logging.INFO):
            handle_evidence(
                "evt-1", "blue-secret", _StubResolver(error=RuntimeError("boom")), TOKEN_MAP
            )
        assert "blue-secret" not in caplog.text


# --- 對真 PG + FakeBackend 的整合（非全棧）--------------------------------

EVENT_ID = "evt-service-0001"
CORE_EVENT = {
    "event_id": EVENT_ID, "exercise_id": "ex-001", "scenario_id": "sqli-01",
    "event_type": "attack.detected", "lifecycle": "firing", "severity": "high",
    "source": "grafana", "team": "red", "technique": "T1190",
    "target": {"service": "vulnerable-app"}, "observed_at": T0.isoformat(),
    "visibility": "public",
    "action_id": None,
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


EVIDENCE_TOKEN_MAP = {"blue-secret": "blue", "red-secret": "red"}


def test_end_to_end_returns_context_window_filtered_by_caller(resolver):
    # blue 看得到 public+blue，看不到 purple
    status, body = handle_evidence(EVENT_ID, "blue-secret", resolver, EVIDENCE_TOKEN_MAP)
    assert status == 200
    vis = {line["visibility"] for line in body["lines"]}
    assert vis == {"public", "blue"}
    assert body["line_count"] >= 2  # 上下文窗，不是單行

    # red 只看得到 public
    _, red_body = handle_evidence(EVENT_ID, "red-secret", resolver, EVIDENCE_TOKEN_MAP)
    assert {line["visibility"] for line in red_body["lines"]} == {"public"}
