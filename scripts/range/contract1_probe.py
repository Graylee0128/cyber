#!/usr/bin/env python3
"""靶機 VM 開機時自驗契約 1 與 response receiver 最小權限例外。

    python3 contract1_probe.py <mgmt_ip> <receiver_ip> <app_ip> <loki_ip> <boot_nonce>

為什麼是獨立檔案而不是塞在 build-vm-target.sh 的 heredoc 裡：位址要代進去就得用
不引號 heredoc，那會連整段 Python 一起做 shell 展開 —— 今天剛好沒有 `$`，明天加一個
f-string 就爆。改走 repo 既有的 `write_files` + base64 慣例（build-golden-target.sh
同法），跳脫地雷整個消失，而且這支變成看得到、審得到的真檔案。

**boot_nonce 的用途**：host 端把同一個 nonce 存成 sidecar 檔。verify-range 比對
console log 裡的 nonce 與 sidecar 是否相同，就知道那份「開機自驗」是不是**現在這顆
VM** 留下的，而不是上一輪的殘骸。沒有這道，契約 1 的證據永遠不會過期，也就永遠
不可能變紅（2026-08-09 code review 發現）。
"""

from __future__ import annotations

import email.utils
import http.client
import socket
import sys
from datetime import datetime, timezone

PORTS = {3100: "Loki", 9090: "Prometheus", 4317: "OTLP"}
RESPONSE_PORT = 8000
DENIED_PORT = 22
APP_PORT = 443
CLOCK_THRESHOLD_MS = 5000


def probe_mgmt_clock(host: str, now=lambda: datetime.now(timezone.utc)) -> float:
    """Compare the VM clock with MGMT Loki's independent HTTP Date clock."""
    before = now()
    conn = http.client.HTTPConnection(host, 3100, timeout=3)
    try:
        conn.request("GET", "/ready")
        response = conn.getresponse()
        response.read()
        date_header = response.getheader("Date")
    finally:
        conn.close()
    after = now()
    if not date_header:
        raise RuntimeError("MGMT Loki response has no Date header")
    mgmt_time = email.utils.parsedate_to_datetime(date_header)
    if mgmt_time.tzinfo is None:
        mgmt_time = mgmt_time.replace(tzinfo=timezone.utc)
    midpoint = before + (after - before) / 2
    return (midpoint - mgmt_time).total_seconds() * 1000


def main() -> None:
    mgmt = sys.argv[1]
    receiver = sys.argv[2]
    app = sys.argv[3]
    loki = sys.argv[4]
    nonce = sys.argv[5] if len(sys.argv) > 5 else "no-nonce"

    print(f"=== SLICE2A-BEGIN（從真 VM 測契約 1）nonce={nonce} ===", flush=True)
    bad = []
    try:
        clock_skew_ms = probe_mgmt_clock(loki)
        print(
            f"VM-CLOCK-SKEW-MS: {clock_skew_ms:+.0f} "
            f"(threshold {CLOCK_THRESHOLD_MS}ms; source=MGMT-Loki-Date)",
            flush=True,
        )
        if abs(clock_skew_ms) > CLOCK_THRESHOLD_MS:
            bad.append("vm-clock")
    except (OSError, RuntimeError, ValueError) as exc:
        bad.append("vm-clock")
        print(f"VM-CLOCK-SKEW-FAIL: {exc}", flush=True)
    for port, name in PORTS.items():
        sock = socket.socket()
        sock.settimeout(3)
        try:
            sock.connect((mgmt, port))
            print(f"契約1 OK: TARGET(VM) -> MGMT {name}:{port} 通", flush=True)
        except OSError as exc:
            bad.append(port)
            print(f"契約1 破: {name}:{port} 不通 ({exc})", flush=True)
        finally:
            sock.close()
    sock = socket.socket()
    sock.settimeout(3)
    try:
        sock.connect((receiver, RESPONSE_PORT))
        print(
            f"契約1 OK: TARGET(VM) -> response receiver "
            f"{receiver}:{RESPONSE_PORT} 通",
            flush=True,
        )
    except OSError as exc:
        bad.append(f"receiver:{RESPONSE_PORT}")
        print(
            f"契約1 破: response receiver {receiver}:{RESPONSE_PORT} 不通 ({exc})",
            flush=True,
        )
    finally:
        sock.close()
    sock = socket.socket()
    sock.settimeout(3)
    try:
        sock.connect((mgmt, RESPONSE_PORT))
        bad.append(f"mgmt:{RESPONSE_PORT}")
        print(
            f"契約1 破: TARGET(VM) -> 非 receiver MGMT "
            f"{mgmt}:{RESPONSE_PORT} 竟然通了",
            flush=True,
        )
    except OSError:
        print(
            f"契約1 OK: TARGET(VM) -> 非 receiver MGMT "
            f"{mgmt}:{RESPONSE_PORT} 不通",
            flush=True,
        )
    finally:
        sock.close()
    sock = socket.socket()
    sock.settimeout(3)
    try:
        sock.connect((mgmt, DENIED_PORT))
        bad.append(DENIED_PORT)
        print(
            f"契約1 破: TARGET(VM) -> MGMT 非 telemetry :{DENIED_PORT} 竟然通了",
            flush=True,
        )
    except OSError:
        print(
            f"契約1 OK: TARGET(VM) -> MGMT 非 telemetry :{DENIED_PORT} 不通",
            flush=True,
        )
    finally:
        sock.close()
    for port, should_reach in ((APP_PORT, True), (DENIED_PORT, False)):
        sock = socket.socket()
        sock.settimeout(3)
        try:
            sock.connect((app, port))
            reached = True
        except OSError:
            reached = False
        finally:
            sock.close()
        if reached == should_reach:
            outcome = "通" if reached else "不通"
            print(f"Z-APP OK: TARGET(VM) -> APP {app}:{port} {outcome}", flush=True)
        else:
            bad.append(f"app:{port}")
            outcome = "竟然通了" if reached else "不通"
            print(f"Z-APP 破: TARGET(VM) -> APP {app}:{port} {outcome}", flush=True)
    verdict = "PASS" if not bad else f"FAIL {bad}"
    print(f"=== SLICE2A-RESULT: {verdict} ===", flush=True)


if __name__ == "__main__":
    main()
