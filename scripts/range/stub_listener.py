#!/usr/bin/env python3
"""Slice 1 range 的極簡 TCP listener（票 #13 / WS6）。

兩個用途：
- mgmt 用它佔住 telemetry :3100/:9090/:4317 與 deny canary :22/:8000；指定
  receiver 另佔 :8000。兩個 :8000 都真的 listen，才能證明只有 receiver IP 獲准。
- target 用它聽 :80 並記錄連入的 source IP，驗六台 red 出現六個可分辨 source IP
  （G0 因 NAT 把六台塌成兩個主機 IP 而退場，這是那條退場理由的直接防呆）。
"""

from __future__ import annotations

import argparse
import email.utils
import socket
import threading


def serve(port: int, record: str | None, http_date: bool = False) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    s.listen(16)
    while True:
        conn, addr = s.accept()
        if record:
            with open(record, "a", encoding="utf-8") as f:
                f.write(addr[0] + "\n")
        if http_date:
            conn.settimeout(1)
            try:
                request = conn.recv(4096)
                if request.startswith((b"GET ", b"HEAD ")):
                    date = email.utils.formatdate(usegmt=True)
                    conn.sendall(
                        f"HTTP/1.1 200 OK\r\nDate: {date}\r\n"
                        "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
                    )
            except (OSError, TimeoutError):
                pass
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ports", required=True, help="逗號分隔的 port，如 3100,9090,4317")
    ap.add_argument("--record", help="把每個連入的 source IP append 到此檔")
    ap.add_argument(
        "--http-date-port",
        type=int,
        help="在指定 listener port 回 HTTP Date，供 VM 驗證獨立 MGMT clock",
    )
    args = ap.parse_args()

    ports = [int(p) for p in args.ports.split(",")]
    threads = [
        threading.Thread(
            target=serve,
            args=(p, args.record, p == args.http_date_port),
            daemon=True,
        )
        for p in ports
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
