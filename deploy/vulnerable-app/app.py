"""刻意脆弱的靶機 app（部署測試用）。

餵三條偵測路徑：
- 每個請求（含注入 payload）寫 JSON log 到共享檔 → Alloy 讀 → Loki（log 路徑，SQLi）
- 失敗登入計數用 **OTLP push** 送到 :4317（Alloy otelcol 收 → remote_write → Prometheus）。
  這是 SA §12 契約 1 的 :4317 OTLP telemetry，取代原本 Prometheus scrape（票 #11）。
- `/exec` 在靶機內生一個 shell 直譯器（帶 PURPLESCOPE_EXEC 標記）→ Falco 抓 execve
  → T1059（票 #9 / Slice 2b-②）。

這不是真有漏洞的服務 —— 它只忠實記錄被打了什麼，讓管線有東西可偵測。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG_PATH = os.environ.get("APP_LOG_PATH", "/var/log/app/app.log")
PORT = int(os.environ.get("APP_PORT", "8080"))

_lock = threading.Lock()
_failed_logins = 0        # 仍保留 /metrics 供除錯；Prometheus 已不 scrape 它（改 OTLP push）
_exec_seq = 0

SQLI_MARKERS = ("' or ", " or 1=1", "union select", "-- ", "'--")

# --- OTLP metrics（票 #11：:4317 push 取代 scrape）---------------------------
# best-effort：OTLP 起不來也不擋 app（登入/日誌/exec 照常）。counter 名 ssh_failed_logins
# → otelcol prometheus exporter 補 _total → Prometheus 上仍是 ssh_failed_logins_total，
# 既有 brute-force PromQL `sum(increase(ssh_failed_logins_total[1m]))` 不用改。
_otel_failed_counter = None


def _init_otel() -> None:
    global _otel_failed_counter
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4317")
    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint, insecure=True),
            export_interval_millis=2000,
        )
        provider = MeterProvider(
            resource=Resource.create({"service.name": "vulnerable-app"}),
            metric_readers=[reader],
        )
        metrics.set_meter_provider(provider)
        meter = metrics.get_meter("vulnerable-app")
        _otel_failed_counter = meter.create_counter(
            "ssh_failed_logins",
            description="Failed login attempts",
        )
        print(f"OTLP metrics → {endpoint}", flush=True)
    except Exception as exc:  # noqa: BLE001 — 遙測 best-effort，絕不擋 app
        print(f"OTLP 初始化失敗（app 照常跑）：{exc}", flush=True)


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
        global _failed_logins, _exec_seq
        parsed = urlparse(self.path)

        if parsed.path == "/metrics":
            self._metrics()
            return

        if parsed.path == "/healthz":
            self._text(200, "ok")
            return

        if parsed.path == "/exec":
            # T1059：在靶機內生一個 shell 直譯器並帶標記，Falco 抓 execve。
            with _lock:
                _exec_seq += 1
                seq = _exec_seq
            marker = f"PURPLESCOPE_EXEC_{seq}"
            try:
                subprocess.run(
                    ["/bin/sh", "-c", f"echo {marker}; id"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception as exc:  # noqa: BLE001
                self._text(500, f"exec error: {exc}")
                return
            _write_log({"ts": _now(), "app": "vulnerable-app", "event": "exec", "marker": marker})
            self._text(200, f"executed {marker}")
            return

        if parsed.path == "/login":
            params = parse_qs(parsed.query)
            username = (params.get("username") or [""])[0]
            source_ip = self.headers.get("X-Forwarded-For", self.client_address[0])
            sqli = _looks_like_sqli(username)

            with _lock:
                _failed_logins += 1
            if _otel_failed_counter is not None:
                _otel_failed_counter.add(1)

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
    _init_otel()
    # 開機寫一行，讓 Loki 一定有這個 stream，Grafana 查詢不會因無 stream 而報錯。
    _write_log({"ts": _now(), "app": "vulnerable-app", "event": "startup"})
    print(f"vulnerable-app listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
