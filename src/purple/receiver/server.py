"""P1 Webhook Receiver 的 HTTP 服務（票 03 緩下來的「HTTP 框架與 port」，
部署測試需要它才落地）。

stdlib http.server —— 刻意不引入 web framework：receiver 的邏輯全在 core.py，
這裡只是把 Grafana 的 webhook POST 接進 ingest_alert。

啟動時連 PostgreSQL、建 schema。POST /webhook 收 Grafana 統一告警，
GET /healthz 給 compose healthcheck。
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from purple.receiver import ingest_alert
from purple.harness.schema import assert_core_event
from purple.response.queue import InMemoryCommandQueue
from purple.store.alerts import AlertRecordStore
from purple.store.db import connect, ensure_schema
from purple.store.events import CoreEventStore
from purple.store.fingerprints import FingerprintIndex

log = logging.getLogger("purple.receiver.server")

PORT = int(os.environ.get("PURPLE_RECEIVER_PORT", "8000"))


class WebhookHandler(BaseHTTPRequestHandler):
    # receiver 與 pull endpoints 同一個 Z-MGMT 行程，queue 不需跨行程同步。
    # 測試可用 subclass 換成自己的 queue，避免共享狀態。
    response_queue = InMemoryCommandQueue()

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._respond(200, {"status": "ok"})
        elif self.path == "/response/commands":
            commands = self.response_queue.claim()
            self._respond(200, {"commands": [command.as_dict() for command in commands]})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in {"/webhook", "/response/report"}:
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._respond(400, {"error": f"bad json: {exc}"})
            return

        if self.path == "/response/report":
            self._store_response(body)
            return

        # 每個請求開一條連線：Grafana webhook 量低，簡單優先於連線池。
        conn = connect()
        try:
            emitted = ingest_alert(
                body,
                events=CoreEventStore(conn),
                records=AlertRecordStore(conn),
                fingerprints=FingerprintIndex(conn),
                response_queue=self.response_queue,
            )
        except Exception as exc:  # 收下故障要現形，別讓 Grafana 一直重試打爆
            log.exception("ingest 失敗")
            self._respond(500, {"error": str(exc)})
            return
        finally:
            conn.close()

        self._respond(200, {"emitted": emitted})

    def _store_response(self, event: dict) -> None:
        try:
            assert_core_event(event)
            if not event["event_type"].startswith("response."):
                raise ValueError("only response.* Core Events are accepted")
            conn = connect()
            try:
                CoreEventStore(conn).append(event)
            finally:
                conn.close()
        except Exception as exc:
            log.exception("response report 拒收")
            self._respond(400, {"error": str(exc)})
            return
        self._respond(200, {"stored": event["event_id"]})

    def _respond(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        return  # 交給我們自己的 logger，不要 stderr 雜訊


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    conn = connect()
    ensure_schema(conn)
    conn.close()
    log.info("receiver 就緒，listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), WebhookHandler).serve_forever()


if __name__ == "__main__":
    main()
