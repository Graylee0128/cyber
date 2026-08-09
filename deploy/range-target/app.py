"""靶機 VM 的受攻擊面（Slice 4 / 票 #9）—— 跑在 Z-TARGET(VLAN20) 的真 VM 內。

與 compose 的 vulnerable-app 不同：這支是給**紅隊容器隔著真 VLAN 打**的，而偵測靠
同一台 VM 內的 Falco（modern-eBPF）看 syscall，不是靠 app 自己判斷。

三個端點：
- `/exec`       生一個帶 PURPLESCOPE_EXEC 標記的 shell → Falco 抓 execve → T1059
- `/readsecret` 讀 /etc/purplescope/secret.txt        → Falco 抓 open   → T1005（SA §7 Scenario 03）
- `/healthz`    存活探測

每個請求寫一行 JSON 到 app log（含 source_ip）。Alloy 把 app log 與 Falco events.json
一起推到 Z-MGMT 的 Loki —— 那條 TARGET→MGMT :3100 就是契約 1 的實用。
app log 裡的 source_ip 也是「六台紅隊 source IP 可分辨」在真環境的觀測點。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

LOG_PATH = os.environ.get("TARGET_LOG_PATH", "/var/log/range-target/app.log")
SECRET_PATH = os.environ.get("TARGET_SECRET_PATH", "/etc/purplescope/secret.txt")
PORT = int(os.environ.get("TARGET_PORT", "80"))

_lock = threading.Lock()
_seq = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _next_marker(kind: str) -> str:
    global _seq
    with _lock:
        _seq += 1
        return f"PURPLESCOPE_{kind}_{_seq}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        source_ip = self.headers.get("X-Forwarded-For", self.client_address[0])

        if path == "/healthz":
            self._text(200, "ok")
            return

        if path == "/exec":
            marker = _next_marker("EXEC")
            try:
                # Falco 看的是這個 execve：sh 帶 PURPLESCOPE_EXEC 標記。
                subprocess.run(
                    ["/bin/sh", "-c", f"echo {marker}; id"],
                    capture_output=True, timeout=5, check=False,
                )
            except Exception as exc:  # noqa: BLE001
                self._text(500, f"exec error: {exc}")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/exec",
                        "source_ip": source_ip, "marker": marker, "outcome": "executed"})
            self._text(200, f"executed {marker}\n")
            return

        if path == "/readsecret":
            # SA §7 Scenario 03：敏感檔存取。open() 會被 Falco 抓到。
            try:
                with open(SECRET_PATH, "rb") as f:
                    size = len(f.read())
            except OSError as exc:
                _write_log({"ts": _now(), "app": "range-target", "path": "/readsecret",
                            "source_ip": source_ip, "outcome": "error", "error": str(exc)})
                self._text(500, f"read error: {exc}")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/readsecret",
                        "source_ip": source_ip, "bytes": size, "outcome": "read"})
            self._text(200, f"read {size} bytes from {SECRET_PATH}\n")
            return

        _write_log({"ts": _now(), "app": "range-target", "path": path,
                    "source_ip": source_ip, "outcome": "not_found"})
        self._text(404, "not found\n")

    def _text(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def main() -> None:
    # 開機寫一行，讓 Loki 一定有這個 stream（查詢不會因無 stream 報錯）。
    _write_log({"ts": _now(), "app": "range-target", "event": "startup"})
    print(f"range-target app listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
