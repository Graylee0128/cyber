"""刻意脆弱的靶機 app（部署測試用）。

它做兩件事，剛好餵飽兩條偵測路徑：
- 把每個請求（含注入的 payload）寫成 JSON log 到共享檔 → Alloy 讀 → Loki（log 路徑）
- 曝露 /metrics 的 Prometheus 計數器 ssh_failed_logins_total → Prometheus（metric 路徑）

這不是真的有漏洞的服務 —— 它只忠實記錄被打了什麼，讓管線有東西可偵測。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG_PATH = os.environ.get("APP_LOG_PATH", "/var/log/app/app.log")
PORT = int(os.environ.get("APP_PORT", "8080"))

_lock = threading.Lock()
_failed_logins = 0

SQLI_MARKERS = ("' or ", " or 1=1", "union select", "-- ", "'--")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _looks_like_sqli(value: str) -> bool:
    low = value.lower()
    return any(m in low for m in SQLI_MARKERS)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _failed_logins
        parsed = urlparse(self.path)

        if parsed.path == "/metrics":
            self._metrics()
            return

        if parsed.path == "/healthz":
            self._text(200, "ok")
            return

        if parsed.path == "/login":
            params = parse_qs(parsed.query)
            username = (params.get("username") or [""])[0]
            source_ip = self.headers.get("X-Forwarded-For", self.client_address[0])
            sqli = _looks_like_sqli(username)

            with _lock:
                _failed_logins += 1

            _write_log(
                {
                    "ts": _now(),
                    "app": "vulnerable-app",
                    "path": "/login",
                    "username": username,
                    "source_ip": source_ip,
                    "sqli_suspected": sqli,
                    "outcome": "denied",
                }
            )
            self._text(401, "login failed")
            return

        self._text(404, "not found")

    def _metrics(self) -> None:
        body = (
            "# HELP ssh_failed_logins_total Failed login attempts\n"
            "# TYPE ssh_failed_logins_total counter\n"
            f"ssh_failed_logins_total {_failed_logins}\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def main() -> None:
    # 開機寫一行，讓 Loki 一定有這個 stream，Grafana 查詢不會因無 stream 而報錯。
    _write_log({"ts": _now(), "app": "vulnerable-app", "event": "startup"})
    print(f"vulnerable-app listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
