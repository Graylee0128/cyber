"""Evaluation Engine v0 —— Evidence API 的 HTTP 服務（票 #14），住 Z-MGMT。

把 #7 的 resolver 包成 `GET /evidence/{event_id}`。stdlib http.server，與 P1
receiver 同慣例：判定/序列化是純函數，HTTP 只是薄殼。

呼叫者身分由**部署時注入的服務 token**換出（`Authorization: Bearer <token>`，
WS7 spec §2）。**clearance 由 token 對應的 identity 決定，呼叫者無法自報**——
沒有任何欄位讓呼叫端直接填身分字串（resolver 的 clearance 表說了算，ADR ②）。
token 走環境變數注入（`PURPLE_EVIDENCE_TOKEN_<IDENTITY>`），與 `deploy/` 既有
設定注入方式一致，不引入新的機密管理機制（WS7 spec §2.4）。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from disclosure import CALLER_CLEARANCE

from purple.evidence.backends import BackendUnavailable, LokiBackend
from purple.evidence.resolver import (
    Caller,
    EvidenceBundle,
    EvidenceError,
    EvidenceNotFound,
    EvidenceResolver,
    UnknownCaller,
)

log = logging.getLogger("purple.evidence.service")

PORT = int(os.environ.get("PURPLE_ENGINE_PORT", "8001"))
TOKEN_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "


def load_service_tokens(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """由部署環境變數建出 token → identity 對照表（純函數，env 可注入好測）。

    命名慣例：`PURPLE_EVIDENCE_TOKEN_<IDENTITY 大寫>`。哪個身分沒設對應變數，
    那個身分就沒有合法 token —— fail closed，不是「沒設定就預設放行」。
    """
    source = env if env is not None else os.environ
    tokens: dict[str, str] = {}
    for identity in CALLER_CLEARANCE:
        value = source.get(f"PURPLE_EVIDENCE_TOKEN_{identity.upper()}")
        if value:
            tokens[value] = identity
    return tokens


def extract_token(headers: Mapping[str, str]) -> str | None:
    """由 `Authorization: Bearer <token>` 取出 token；其餘欄位一律忽略 ——

    身分只能經 token 換出，沒有任何「呼叫端直接填身分」的欄位（舊的
    `X-Purple-Identity` 就算送了也沒有作用，見 `TestSelfReportProtection`）。
    """
    value = headers.get(TOKEN_HEADER, "")
    if value.startswith(BEARER_PREFIX):
        return value[len(BEARER_PREFIX):] or None
    return None


def resolve_identity(token: str | None, token_map: Mapping[str, str]) -> str | None:
    """token 換出 identity。查無此 token → None，絕不回退成任何預設身分。"""
    if not token:
        return None
    return token_map.get(token)


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


def handle_evidence(
    event_id: str,
    token: str | None,
    resolver: Any,
    token_map: Mapping[str, str],
) -> tuple[int, dict[str, Any]]:
    """解析一次 evidence 請求，回傳 (HTTP status, body)（判定邏輯，可獨立測）。

    狀態對應是刻意明確的：
    - 沒帶 token           → 400（不預設成某個身分，更不預設成看得到全部）
    - token 查無對應身分   → 403（fail loud；呼叫端無法用任何欄位頂替身分）
    - 查無此 event_id      → 404
    - 後端未就緒（無 Loki） → 503（明確告知，不回空上下文假裝成功）

    identity 的唯一來源是 `token_map[token]` —— 沒有參數讓呼叫端直接填身分，
    錯誤訊息與 log 也都不回顯 token 本身（token 不進 log）。
    """
    if not token:
        return 400, {"error": f"missing {TOKEN_HEADER} bearer token"}

    identity = resolve_identity(token, token_map)
    if identity is None:
        return 403, {"error": "unknown or invalid service token"}

    try:
        bundle = resolver.resolve(event_id, caller=Caller(identity))
    except UnknownCaller as exc:
        return 403, {"error": str(exc)}
    except EvidenceNotFound as exc:
        return 404, {"error": str(exc)}
    except BackendUnavailable as exc:
        return 503, {"error": str(exc)}
    except EvidenceError as exc:
        # 我們自己的資料完整性錯誤（如 observed_at 壞掉）：訊息可回，無路徑/金鑰。
        return 500, {"error": str(exc)}
    except Exception:
        # 非預期錯誤（如 DB 連線）：**不**把 str(exc) 回給呼叫者，避免洩漏內部細節。
        log.exception("evidence 解析非預期失敗 event_id=%s", event_id)
        return 500, {"error": "internal error"}

    return 200, render_bundle(bundle)


class EvidenceHandler(BaseHTTPRequestHandler):
    resolver: Any = None  # 由 main() 注入
    token_map: Mapping[str, str] = {}  # 由 main() 注入：token → identity

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

        token = extract_token(self.headers)
        status, body = handle_evidence(event_id, token, self.resolver, self.token_map)
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
    EvidenceHandler.token_map = load_service_tokens()
    if not EvidenceHandler.token_map:
        log.warning("沒有任何 PURPLE_EVIDENCE_TOKEN_* 設定；此服務會拒絕所有請求")

    log.info("evaluation-engine（Evidence API）就緒，listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), EvidenceHandler).serve_forever()


if __name__ == "__main__":
    main()
