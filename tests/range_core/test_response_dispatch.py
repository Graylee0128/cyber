"""`HttpResponseDispatcher`（#51／WS3 spec §5.2）—— Range Core 呼叫 Z-MGMT
的 client 端。用一台真的本機 HTTP server 當 Z-MGMT 的替身，同
`tests/response/test_enqueue_endpoint.py` 的手法反過來用。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from purple.store.events import CoreEventStore

from range_core.response_dispatch import HttpResponseDispatcher

EX = "ex-dispatch-1"
TOKEN = "dispatch-test-token"


def _write_event(conn, event_id, *, source_ip="10.167.30.11", service="range-target"):
    CoreEventStore(conn).append(
        {
            "event_id": event_id,
            "exercise_id": EX,
            "scenario_id": "scenario-01",
            "event_type": "attack.detected",
            "lifecycle": "firing",
            "severity": "high",
            "source": "grafana",
            "team": "red",
            "technique": "T1190",
            "target": {"source_ip": source_ip, "service": service} if source_ip else {},
            "observed_at": "2026-08-14T00:00:00+00:00",
            "visibility": "public",
            "action_id": None,
        }
    )


class _RecordingUpstream(BaseHTTPRequestHandler):
    """假冒 Z-MGMT 的 `/response/enqueue`：記下收到什麼，回傳可設定的狀態碼。"""

    received: list[tuple[dict, str | None]] = []
    status_to_return = 202

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("Authorization")
        type(self).received.append((body, auth))
        self.send_response(self.status_to_return)
        self.send_header("Content-Type", "application/json")
        payload = b'{"enqueued": true}'
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return


@pytest.fixture
def upstream():
    _RecordingUpstream.received = []
    _RecordingUpstream.status_to_return = 202
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingUpstream)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", _RecordingUpstream
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestSuccessfulDispatch:
    def test_dispatch_returns_true_on_2xx(self, pg_connection, upstream):
        base_url, _ = upstream
        _write_event(pg_connection, "evt-1")
        dispatcher = HttpResponseDispatcher(conn=pg_connection, base_url=base_url, token=TOKEN)

        assert dispatcher.dispatch(EX, "evt-1") is True

    def test_sends_the_bearer_token_and_core_event_fields(self, pg_connection, upstream):
        base_url, upstream_handler = upstream
        _write_event(pg_connection, "evt-1", source_ip="10.167.30.12", service="range-target")
        dispatcher = HttpResponseDispatcher(conn=pg_connection, base_url=base_url, token=TOKEN)

        dispatcher.dispatch(EX, "evt-1")

        [(body, auth)] = upstream_handler.received
        assert auth == f"Bearer {TOKEN}"
        assert body["event_id"] == "evt-1"
        assert body["source_ip"] == "10.167.30.12"
        assert body["exercise_id"] == EX
        assert body["technique"] == "T1190"


class TestFailureIsFalseNotAnException:
    def test_non_2xx_from_upstream_is_false(self, pg_connection, upstream):
        base_url, upstream_handler = upstream
        upstream_handler.status_to_return = 403
        _write_event(pg_connection, "evt-1")
        dispatcher = HttpResponseDispatcher(conn=pg_connection, base_url=base_url, token=TOKEN)

        assert dispatcher.dispatch(EX, "evt-1") is False

    def test_unreachable_upstream_is_false(self, pg_connection):
        _write_event(pg_connection, "evt-1")
        # 埠 1 幾乎保證連不上（特權埠、沒人聽）。
        dispatcher = HttpResponseDispatcher(
            conn=pg_connection, base_url="http://127.0.0.1:1", token=TOKEN, timeout_s=1.0
        )

        assert dispatcher.dispatch(EX, "evt-1") is False

    def test_unknown_event_is_false(self, pg_connection, upstream):
        base_url, upstream_handler = upstream
        dispatcher = HttpResponseDispatcher(conn=pg_connection, base_url=base_url, token=TOKEN)

        assert dispatcher.dispatch(EX, "evt-does-not-exist") is False
        assert upstream_handler.received == []  # 沒有東西可派送，根本不該打出去

    def test_event_missing_source_ip_is_false(self, pg_connection, upstream):
        base_url, upstream_handler = upstream
        _write_event(pg_connection, "evt-1", source_ip=None)
        dispatcher = HttpResponseDispatcher(conn=pg_connection, base_url=base_url, token=TOKEN)

        assert dispatcher.dispatch(EX, "evt-1") is False
        assert upstream_handler.received == []

    def test_missing_configuration_is_false_not_an_exception(self, pg_connection, monkeypatch):
        monkeypatch.delenv("PURPLE_RESPONSE_URL", raising=False)
        monkeypatch.delenv("RANGE_CORE_RESPONSE_TOKEN", raising=False)
        _write_event(pg_connection, "evt-1")
        dispatcher = HttpResponseDispatcher(conn=pg_connection, base_url=None, token=None)

        assert dispatcher.dispatch(EX, "evt-1") is False
