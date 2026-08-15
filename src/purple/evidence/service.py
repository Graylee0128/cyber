"""Evaluation Engine v0 —— Evidence API 的 HTTP 服務（票 #14），住 Z-MGMT。

把 #7 的 resolver 包成 `GET /evidence/{event_id}`。stdlib http.server，與 P1
receiver 同慣例：判定/序列化是純函數，HTTP 只是薄殼。

呼叫者身分由**部署時注入的服務 token**換出（`Authorization: Bearer <token>`，
WS7 spec §2）。**clearance 由 token 對應的 identity 決定，呼叫者無法自報**——
沒有任何欄位讓呼叫端直接填身分字串（resolver 的 clearance 表說了算，ADR ②）。
token 走環境變數注入（`PURPLE_EVIDENCE_TOKEN_<IDENTITY>`），與 `deploy/` 既有
設定注入方式一致，不引入新的機密管理機制（WS7 spec §2.4）。換出邏輯本身住
`disclosure.identity`，與 Range Core 那個出口共用一份 —— 本檔只擁有自己的
token 命名空間（prefix），不擁有規則。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from disclosure import (
    BEARER_PREFIX,
    CALLER_CLEARANCE,
    TOKEN_HEADER,
    build_label_map,
    extract_token,
    project_fields,
    resolve_identity,
)
from disclosure import load_service_tokens as _load_service_tokens
from disclosure.detection_rules import load_rule_titles
from disclosure.fields import FIELD_MASKING

from purple.evidence.backends import BackendUnavailable, LokiBackend
from purple.evidence.copilot import generate_copilot_summary
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

#: 本服務的 token 環境變數 prefix。換出邏輯共用 `disclosure.identity`，
#: 但 token 命名空間是本服務自己的 —— Evidence 的 token 換不出 Range Core 的身分。
TOKEN_ENV_PREFIX = "PURPLE_EVIDENCE_TOKEN_"


def load_service_tokens(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """由 `PURPLE_EVIDENCE_TOKEN_<IDENTITY 大寫>` 建出 token → identity 對照表。

    哪個身分沒設對應變數，那個身分就沒有合法 token —— fail closed，不是
    「沒設定就預設放行」。規則本體在 `disclosure.identity`（兩個出口共用一份）。
    """
    return _load_service_tokens(TOKEN_ENV_PREFIX, env)


def render_bundle(
    bundle: EvidenceBundle,
    *,
    caller_clearance: int,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """把 EvidenceBundle 序列化成 JSON-able dict（純函數）。

    刻意只吐 bundle 帶的欄位 —— 沒有 backend、沒有原始 query。遙測細節不外流
    （ADR ④）；bundle 本身就已經不帶那些。

    `caller_clearance` 是**必填**的：套用 `disclosure` 的欄位級遮蔽（#49）需要它，
    而給它預設值等於讓「忘記傳」變成一次靜默的完整揭露 —— 正是本票要消滅的那種
    「漏遮不會有錯誤訊號」。**遮蔽在這裡發生，也就是 API 回應的組裝**，不是前端
    渲染 —— 前端遮是假的，devtools 打開就看到。
    """
    body = {
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

    return project_fields(body, caller_clearance, labels=labels)


def build_detection_labels(titles: Sequence[str] | None = None) -> dict[str, dict[str, str]]:
    """`rule` 欄位的匿名標籤表。標題來源與排序見 `disclosure.detection_rules`。"""
    policy = FIELD_MASKING["rule"]
    names = load_rule_titles() if titles is None else titles
    return {"rule": build_label_map(names, policy.label_prefix or "Detection")}


def handle_evidence(
    event_id: str,
    token: str | None,
    resolver: Any,
    token_map: Mapping[str, str],
    labels: Mapping[str, Mapping[str, str]] | None = None,
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

    # 查不到 clearance 就當最低一級（0）—— resolver 早該擋下這種身分，萬一沒有，
    # 少看見一點永遠好過多看見一點。
    return 200, render_bundle(
        bundle, caller_clearance=CALLER_CLEARANCE.get(identity, 0), labels=labels
    )


def handle_copilot_summary(
    body: dict[str, Any],
    token: str | None,
    token_map: Mapping[str, str],
) -> tuple[int, dict[str, Any]]:
    """`POST /copilot/summary`（#133）—— 只給教官用，其餘身分一律 403。

    跟 `handle_evidence` 同一種狀態對應原則：沒帶 token → 400；查無身分或
    身分不是 instructor → 403（fail loud，不是「非教官就看不到某些欄位」，
    是「非教官整條路都不通」——這條端點的存在本身，比它回什麼內容更敏感，
    洩漏「教官在看哪些玩家」這件事本身就違反 #133 的角色邊界）。

    `player_statuses` 是呼叫端已經準備好的快照，這裡不查任何資料庫——AI 摘要
    生成本身可能逾時，逾時只讓這次回應少一段摘要，不影響其餘端點。
    """
    if not token:
        return 400, {"error": f"missing {TOKEN_HEADER} bearer token"}

    identity = resolve_identity(token, token_map)
    if identity != "instructor":
        return 403, {"error": "copilot summary is instructor-only"}

    player_statuses = body.get("player_statuses")
    if not isinstance(player_statuses, list):
        return 400, {"error": "missing or invalid player_statuses (must be a list)"}

    summary = generate_copilot_summary(player_statuses)
    return 200, {"summary": summary}


class EvidenceHandler(BaseHTTPRequestHandler):
    resolver: Any = None  # 由 main() 注入
    token_map: Mapping[str, str] = {}  # 由 main() 注入：token → identity
    labels: Mapping[str, Mapping[str, str]] = {}  # 由 main() 注入：欄位匿名標籤

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
        status, body = handle_evidence(
            event_id, token, self.resolver, self.token_map, self.labels
        )
        self._respond(status, body)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/copilot/summary":
            self._respond(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request_body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON body"})
            return
        if not isinstance(request_body, dict):
            self._respond(400, {"error": "body must be a JSON object"})
            return

        token = extract_token(self.headers)
        status, body = handle_copilot_summary(request_body, token, self.token_map)
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

    EvidenceHandler.labels = build_detection_labels()
    if not EvidenceHandler.labels["rule"]:
        log.warning("偵測規則清單是空的；clearance 不足的呼叫者將完全看不到 rule 欄位")

    log.info("evaluation-engine（Evidence API）就緒，listening on :%d", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), EvidenceHandler).serve_forever()


if __name__ == "__main__":
    main()
