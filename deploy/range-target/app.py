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
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

LOG_PATH = os.environ.get("TARGET_LOG_PATH", "/var/log/range-target/app.log")
SECRET_PATH = os.environ.get("TARGET_SECRET_PATH", "/etc/purplescope/secret.txt")
PORT = int(os.environ.get("TARGET_PORT", "80"))
HEARTBEAT_INTERVAL_S = 30

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


def emit_alloy_heartbeat() -> None:
    """End-to-end canary：只有 Alloy 還能轉送時，這筆才會出現在 Loki。"""
    _write_log({"ts": _now(), "app": "range-target", "event": "alloy.heartbeat"})


def _alloy_heartbeat_loop() -> None:
    while True:
        emit_alloy_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL_S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        # source_ip 一律取 TCP 連線的對端，**絕不看 X-Forwarded-For**。
        # 這條拓樸裡紅隊直連靶機，中間沒有任何 proxy —— 信任 XFF 換不到好處，卻讓
        # 「六台紅隊 source IP 可分辨」這個契約證據變成送個 header 就能偽造的東西。
        # 歸屬證據不能由被歸屬方控制（2026-08-09 code review）。
        source_ip = self.client_address[0]

        if path == "/healthz":
            self._text(200, "ok")
            return

        if path == "/uncovered":
            # 「有遙測、但沒有任何 Grafana 規則覆蓋」的動作 —— 決定性測試的真環境素材。
            # Falco 會抓到並推進 Loki（遙測在），但 deploy/grafana 那邊刻意沒有對應規則，
            # 所以不會有告警、不會有 Core Event。這正是 ADR ③ 要能分辨的
            # 「看得到卻沒偵測到」= DETECTION_GAP，而不是「根本沒看到」= VISIBILITY_GAP。
            marker = _next_marker("UNCOVERED")
            try:
                subprocess.run(
                    ["/bin/sh", "-c", f"echo {marker}; id"],
                    capture_output=True, timeout=5, check=False,
                )
            except Exception as exc:  # noqa: BLE001
                self._text(500, f"exec error: {exc}")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/uncovered",
                        "source_ip": source_ip, "marker": marker, "outcome": "executed"})
            self._text(200, f"executed {marker}\n")
            return

        if path == "/exec":
            marker = _next_marker("EXEC")
            try:
                # Falco 看的是這個 execve。來源 IP 來自 TCP 對端，放進 cmdline 後由
                # Grafana LogQL 擷取為 source_ip label；agent 不必也不得自行猜封鎖對象。
                subprocess.run(
                    ["/bin/sh", "-c", f"echo {marker} SOURCE_IP={source_ip}; id"],
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
    threading.Thread(target=_alloy_heartbeat_loop, daemon=True).start()
    print(f"range-target app listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
