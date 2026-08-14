"""P1 Webhook Receiver 的 HTTP 服務（票 03 緩下來的「HTTP 框架與 port」，
部署測試需要它才落地）。

stdlib http.server —— 刻意不引入 web framework：receiver 的邏輯全在 core.py，
這裡只是把 Grafana 的 webhook POST 接進 ingest_alert。

啟動時連 PostgreSQL、建 schema。POST /webhook 收 Grafana 統一告警，
GET /healthz 給 compose healthcheck。POST /response/enqueue 收 Range Core
派來的封鎖命令（#51，見該端點的 docstring）。
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from disclosure.identity import extract_token
from purple.receiver import ingest_alert
from purple.harness.schema import assert_core_event
from purple.receiver.exercise_context import RunningExerciseLookup
from purple.response.queue import InMemoryCommandQueue, ResponseCommand
from purple.store.alerts import AlertRecordStore
from purple.store.db import connect, ensure_schema
from purple.store.events import CoreEventStore
from purple.store.fingerprints import FingerprintIndex

log = logging.getLogger("purple.receiver.server")

PORT = int(os.environ.get("PURPLE_RECEIVER_PORT", "8000"))

# 演練預設關閉：自動 enqueue 只給測試載具／demo 用，必須明確開啟（票 48）。
# 開啟時 enqueue 出的命令 triggered_by="auto"，計分（#33）不採計。
AUTO_RESPONSE = os.environ.get("PURPLE_AUTO_RESPONSE", "false").lower() == "true"

#: Range Core 打 `/response/enqueue` 要出示的 token（#51）。跟其餘服務的
#: `*_TOKEN_<IDENTITY>` 命名慣例不同——這裡只有唯一一個合法呼叫者
#: （Range Core 自己），不是要在多個身分裡換出一個，所以是單一共用密鑰，
#: 不是身分對照表。
ENQUEUE_TOKEN_ENV = "RANGE_CORE_RESPONSE_TOKEN"

#: `ResponseCommand` 的欄位裡，`/response/enqueue` 的呼叫端（Range Core）
#: 該提供哪些——`action`／`triggered_by` 由這個端點自己決定，不採信
#: 呼叫端傳的值（見 `_handle_enqueue`）。
_ENQUEUE_REQUIRED_FIELDS = (
    "event_id", "source_ip", "exercise_id", "scenario_id", "severity", "technique", "service",
)


class WebhookHandler(BaseHTTPRequestHandler):
    # receiver 與 pull endpoints 同一個 Z-MGMT 行程，queue 不需跨行程同步。
    # 測試可用 subclass 換成自己的 queue，避免共享狀態。
    response_queue = InMemoryCommandQueue()
    # 測試載具（如 test_http_pull 的全鏈路測試）可用 subclass 覆寫成 True；
    # 生產預設跟 AUTO_RESPONSE 走，演練不自動 enqueue（票 48）。
    auto_response = AUTO_RESPONSE

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._respond(200, {"status": "ok"})
        elif self.path == "/response/commands":
            commands = self.response_queue.claim()
            self._respond(200, {"commands": [command.as_dict() for command in commands]})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path not in {"/webhook", "/response/report", "/response/enqueue"}:
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._respond(400, {"error": f"bad json: {exc}"})
            return

        if self.path == "/response/enqueue":
            self._handle_enqueue(body)
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
                exercise_id=RunningExerciseLookup(conn).require_id(),
                auto_response=self.auto_response,
            )
        except Exception as exc:  # 收下故障要現形，別讓 Grafana 一直重試打爆
            log.exception("ingest 失敗")
            self._respond(500, {"error": str(exc)})
            return
        finally:
            conn.close()

        self._respond(200, {"emitted": emitted})

    def _handle_enqueue(self, body: dict) -> None:
        """Range Core 派送 `contain` 動作的唯一入口（#51／WS3 spec §5.2）。

        身分驗證跟其餘端點是**完全不同的機制**——`/webhook` 認 Grafana、
        `/response/report` 認 agent，這裡認 Range Core，用單一共用密鑰而非
        身分對照表（Range Core 是這條路徑唯一的合法呼叫者）。

        `triggered_by` 永遠被這裡覆寫成 `"manual"`，**不採信呼叫端傳的值**
        ——這個端點本身就是「人在迴圈」那條路徑的定義：能打到這裡代表
        Blue Action 已經落地過一次（Range Core 端強制的順序），觸發來源
        不可能是別的。
        """
        expected = os.environ.get(ENQUEUE_TOKEN_ENV)
        if not expected:
            self._respond(503, {"error": "enqueue token not configured on this deployment"})
            return
        token = extract_token(self.headers)
        if not token or not hmac.compare_digest(token, expected):
            self._respond(403, {"error": "invalid or missing enqueue token"})
            return

        missing = [field for field in _ENQUEUE_REQUIRED_FIELDS if field not in body]
        if missing:
            self._respond(400, {"error": f"missing fields: {missing}"})
            return

        command = ResponseCommand(
            event_id=body["event_id"],
            source_ip=body["source_ip"],
            exercise_id=body["exercise_id"],
            scenario_id=body["scenario_id"],
            severity=body["severity"],
            technique=body["technique"],
            service=body["service"],
            triggered_by="manual",
        )
        self.response_queue.enqueue(command)
        self._respond(202, {"enqueued": True, "event_id": command.event_id})

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
