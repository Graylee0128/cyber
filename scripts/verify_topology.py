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

兩種模式：
  --compose：對 docker compose 的網段歸屬驗 Docker membership 能強制的區規則（票 #15）。
             不需真網段，本機／CI 都能跑。方向性（契約 2）等真防火牆屬 #13。
  --mgmt/--target：對真四區網段 socket 實測四條契約（workstream 6 / #13）。

用法：
    python scripts/verify_topology.py --compose
    python scripts/verify_topology.py --from-zone target --mgmt 10.167.10.10 --target 10.167.20.10
退出碼 0＝可驗部分全通過；1＝有契約不成立；2＝環境未就位。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# 讓腳本能找到 src/ 的套件（未安裝時）。
sys.path.insert(0, __file__.rsplit("scripts", 1)[0] + "src")

from purple.topology_check import (  # noqa: E402
    check_mgmt_to_target_blocked,
    check_red_to_mgmt_denied,
    check_target_to_mgmt,
    check_zone_assignments,
)


def _verify_compose() -> int:
    """對 docker compose 解析後的網段歸屬，驗可強制的四區規則（票 #15）。"""
    try:
        raw = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        print("環境未就位：找不到 docker CLI。", file=sys.stderr)
        return 2
    if raw.returncode != 0:
        print(f"環境未就位：docker compose config 失敗：\n{raw.stderr}", file=sys.stderr)
        return 2

    cfg = json.loads(raw.stdout)
    service_networks = {
        name: set((svc.get("networks") or {}).keys())
        for name, svc in cfg.get("services", {}).items()
    }
    fails = check_zone_assignments(service_networks)
    if fails:
        print("compose 區歸屬未通過：", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("compose 區歸屬通過（Docker membership 能強制的部分）。")
    print("  未涵蓋（委派 #13）：契約 2 方向、真 VLAN/macvlan、六台 kali source IP、真防火牆。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", action="store_true", help="驗 docker compose 網段歸屬（票 #15）")
    parser.add_argument("--mgmt", help="Z-MGMT 節點 IP")
    parser.add_argument("--target", help="Z-TARGET 節點 IP")
    parser.add_argument("--from-zone", choices=["target", "red"], default="target")
    args = parser.parse_args(argv)

    if args.compose:
        return _verify_compose()

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
