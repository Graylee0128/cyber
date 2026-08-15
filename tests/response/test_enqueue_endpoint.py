"""`POST /response/enqueue`（#51／WS3 spec §5.2）—— Range Core 派送 `contain`
的唯一入口。真的起一台 HTTP server 打，跟 `test_http_pull.py` 同手法。
"""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from purple.receiver.server import WebhookHandler
from purple.response.queue import InMemoryCommandQueue

TOKEN = "range-core-enqueue-secret"
PAYLOAD = {
    "event_id": "evt-1",
    "source_ip": "10.167.30.11",
    "exercise_id": "ex-1",
    "scenario_id": "scenario-01",
    "severity": "high",
    "technique": "T1190",
    "service": "range-target",
}


def _post(url, body, headers=None):
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - localhost test server
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("RANGE_CORE_RESPONSE_TOKEN", TOKEN)
    queue = InMemoryCommandQueue()

    class Handler(WebhookHandler):
        response_queue = queue

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", queue
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestAuth:
    def test_missing_token_is_403(self, server):
        base_url, queue = server
        status, body = _post(f"{base_url}/response/enqueue", PAYLOAD)
        assert status == 403
        assert queue.claim() == []

    def test_wrong_token_is_403(self, server):
        base_url, queue = server
        status, _ = _post(
            f"{base_url}/response/enqueue", PAYLOAD,
            headers={"Authorization": "Bearer not-the-real-token"},
        )
        assert status == 403
        assert queue.claim() == []

    def test_token_not_configured_is_503_not_a_silent_pass(self, server, monkeypatch):
        monkeypatch.delenv("RANGE_CORE_RESPONSE_TOKEN", raising=False)
        base_url, queue = server
        status, _ = _post(
            f"{base_url}/response/enqueue", PAYLOAD,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert status == 503
        assert queue.claim() == []

    def test_correct_token_is_accepted(self, server):
        base_url, _ = server
        status, _ = _post(
            f"{base_url}/response/enqueue", PAYLOAD,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert status == 202


class TestErrorsDoNotLeakInternals:
    """錯誤回應不得夾帶內部細節。

    `/webhook` 與 `/response/report` 的 except 區段涵蓋了開資料庫連線 ——
    psycopg 的連線錯誤訊息**帶著完整 DSN（含帳密）**，直接 `str(exc)` 回去
    等於把資料庫密碼回給呼叫者。同
    `tests/evidence/test_service.py::test_unexpected_error_is_500_without_leaking_details`。
    """

    def test_webhook_failure_does_not_echo_the_dsn(self, server, monkeypatch):
        base_url, _ = server
        monkeypatch.setenv(
            "PURPLE_PG_DSN",
            "postgresql://purple:super-secret-password@db.internal.invalid:5432/purple",
        )

        status, body = _post(f"{base_url}/webhook", {"alerts": []})

        assert status == 500
        assert "super-secret-password" not in json.dumps(body)
        assert "db.internal.invalid" not in json.dumps(body)

    def test_contract_violation_still_explains_itself(self, server):
        """相對照：契約違規的訊息**要**回給呼叫者，否則對方修不動。"""
        base_url, _ = server

        status, body = _post(f"{base_url}/response/report", {"event_type": "attack.detected"})

        assert status == 400
        assert body["error"]


class TestPayloadHandling:
    def test_missing_field_is_400(self, server):
        base_url, queue = server
        incomplete = {k: v for k, v in PAYLOAD.items() if k != "source_ip"}
        status, body = _post(
            f"{base_url}/response/enqueue", incomplete,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert status == 400
        assert "source_ip" in body["error"]
        assert queue.claim() == []

    def test_enqueued_command_is_triggered_by_manual(self, server):
        """核心正確性：這條路徑就是「人在迴圈」本身，觸發來源永遠是 manual
        ——**不採信呼叫端傳的值**，就算呼叫端試著塞 triggered_by=auto 也一樣。"""
        base_url, queue = server
        _post(
            f"{base_url}/response/enqueue",
            {**PAYLOAD, "triggered_by": "auto", "action": "something-else"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )

        [command] = queue.claim()
        assert command.triggered_by == "manual"
        assert command.action == "block"
        assert command.event_id == "evt-1"
        assert command.source_ip == "10.167.30.11"
