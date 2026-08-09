"""Evaluation Engine v0 —— Evidence API 的 HTTP 服務（票 #14），住 Z-MGMT。

把 #7 的 resolver 包成 `GET /evidence/{event_id}`。stdlib http.server，與 P1
receiver 同慣例：判定/序列化是純函數，HTTP 只是薄殼。

呼叫者身分由 `X-Purple-Identity` header 帶入。**clearance 由 identity 決定，
呼叫者無法自報等級**（resolver 的 clearance 表說了算，ADR ②）。真正的部署會在
zone 邊界以 auth/mTLS 建立 identity；v0 先信任 header。
"""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from purple.evidence.backends import BackendUnavailable, LokiBackend
from purple.evidence.resolver import (
    Caller,
    EvidenceBundle,
    EvidenceNotFound,
    EvidenceResolver,
    UnknownCaller,
)

log = logging.getLogger("purple.evidence.service")

PORT = int(os.environ.get("PURPLE_ENGINE_PORT", "8001"))
IDENTITY_HEADER = "X-Purple-Identity"


def render_bundle(bundle: EvidenceBundle) -> dict[str, Any]:
    """把 EvidenceBundle 序列化成 JSON-able dict（純函數）。

    刻意只吐 bundle 帶的欄位 —— 沒有 backend、沒有原始 query。遙測細節不外流
    （ADR ④）；bundle 本身就已經不帶那些。
    """
    return {
        "event_id": bundle.event_id,
        "rule": bundle.rule,
        "window_start": bundle.window_start.isoformat(),
        "window_end": bundle.window_end.isoformat(),
        "line_count": bundle.line_count,
        "lines": [
            {
                "timestamp": line.timestamp.isoformat(),
                "line": line.line,
                "visibility": line.visibility,
                "source": line.source,
            }
            for line in bundle.lines
        ],
    }


def handle_evidence(event_id: str, identity: str | None, resolver: Any) -> tuple[int, dict[str, Any]]:
    """解析一次 evidence 請求，回傳 (HTTP status, body)（判定邏輯，可獨立測）。

    狀態對應是刻意明確的：
    - 沒帶身分            → 400（不預設成某個身分，更不預設成看得到全部）
    - 身分不認得          → 403（fail loud）
    - 查無此 event_id     → 404
    - 後端未就緒（無 Loki）→ 503（明確告知，不回空上下文假裝成功）
    """
    if not identity:
        return 400, {"error": f"missing {IDENTITY_HEADER} header"}

    try:
        bundle = resolver.resolve(event_id, caller=Caller(identity))
    except UnknownCaller as exc:
        return 403, {"error": str(exc)}
    except EvidenceNotFound as exc:
        return 404, {"error": str(exc)}
    except BackendUnavailable as exc:
        return 503, {"error": str(exc)}

    return 200, render_bundle(bundle)


class EvidenceHandler(BaseHTTPRequestHandler):
    resolver: Any = None  # 由 main() 注入

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._respond(200, {"status": "ok"})
            return

        prefix = "/evidence/"
        if not path.startswith(prefix):
            self._respond(404, {"error": "not found"})
            return

        event_id = unquote(path[len(prefix):])
        if not event_id:
            self._respond(400, {"error": "missing event_id"})
            return

        identity = self.headers.get(IDENTITY_HEADER)
        status, body = handle_evidence(event_id, identity, self.resolver)
        self._respond(status, body)

    def _respond(self, code: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args) -> None:
        return


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    from purple.store.alerts import AlertRecordStore
    from purple.store.db import connect, ensure_schema
    from purple.store.events import CoreEventStore

    conn = connect()
    ensure_schema(conn)

    backend = LokiBackend(base_url=os.environ.get("PURPLE_LOKI_URL", "http://loki:3100"))
    resolver = EvidenceResolver(
        records=AlertRecordStore(conn),
        events=CoreEventStore(conn),
        backend=backend,
    )
    EvidenceHandler.resolver = resolver

    log.info("evaluation-engine（Evidence API）就緒，listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), EvidenceHandler).serve_forever()


if __name__ == "__main__":
    main()
