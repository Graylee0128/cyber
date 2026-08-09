#!/usr/bin/env python3
"""靶機 VM 開機時自驗契約 1（TARGET → MGMT 三個 port 通），由 cloud-init 呼叫。

    python3 contract1_probe.py <mgmt_ip> <boot_nonce>

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

import socket
import sys

PORTS = {3100: "Loki", 9090: "Prometheus", 4317: "OTLP"}


def main() -> None:
    mgmt = sys.argv[1]
    nonce = sys.argv[2] if len(sys.argv) > 2 else "no-nonce"

    print(f"=== SLICE2A-BEGIN（從真 VM 測契約 1）nonce={nonce} ===", flush=True)
    bad = []
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
    verdict = "PASS" if not bad else f"FAIL {bad}"
    print(f"=== SLICE2A-RESULT: {verdict} ===", flush=True)


if __name__ == "__main__":
    main()
