#!/usr/bin/env python3
"""四條跨世代契約的可重複驗收 CLI（票 12 / issue #13）。

環境斷言，非 TDD。對真實網段動作；網段建置屬 workstream 6。缺環境時明確失敗，
**絕不 fake pass** —— 永遠綠的拓樸檢查等於沒有檢查。邏輯在 purple.topology_check。

四條契約（SA §12.2）：
  1. TARGET → MGMT 的 :3100 / :9090 / :4317 通
  2. MGMT → TARGET 反向不通（response 走 agent pull 的整個理由）
  3. RED → MGMT deny all
  4. collector（Alloy / Falco / response agent）全部在 target 側
外加：六台 kali 各打一次，事件中應出現六個可分辨的 source IP。

用法（在具四區網段的環境）：
    python scripts/verify_topology.py --from-zone target --mgmt 10.167.10.10 --target 10.167.20.10
退出碼 0＝本區可驗契約全通過；1＝有契約不成立；2＝環境未就位。
"""

from __future__ import annotations

import argparse
import sys

# 讓腳本能找到 src/ 的套件（未安裝時）。
sys.path.insert(0, __file__.rsplit("scripts", 1)[0] + "src")

from purple.topology_check import (  # noqa: E402
    check_mgmt_to_target_blocked,
    check_red_to_mgmt_denied,
    check_target_to_mgmt,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mgmt", help="Z-MGMT 節點 IP")
    parser.add_argument("--target", help="Z-TARGET 節點 IP")
    parser.add_argument("--from-zone", choices=["target", "red"], default="target")
    args = parser.parse_args(argv)

    if not args.mgmt or not args.target:
        print(
            "環境未就位：需要四區網段與各節點 IP（workstream 6）。\n"
            "本檢查刻意不在缺環境時回報成功。",
            file=sys.stderr,
        )
        return 2

    fails: list[str] = []
    if args.from_zone == "target":
        fails += check_target_to_mgmt(args.mgmt)
        fails += check_mgmt_to_target_blocked(args.target)
    else:  # red
        fails += check_red_to_mgmt_denied(args.mgmt)

    if fails:
        print("拓樸契約未通過：", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("拓樸契約通過（本區可驗部分）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
