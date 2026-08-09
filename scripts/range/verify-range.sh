#!/usr/bin/env bash
# 票 #13 —— 從各區實測跨世代契約 + 六台 red source IP 可分辨。
#
# 兩種佈局都認得（自動偵測，同一支驗證器）：
#   netns 模式（Slice 1 / CI）：靶機與紅隊都是 network namespace
#   VM 模式  （Slice 4 / 大主機）：靶機是真 VM(10.167.20.10)，紅隊是真容器
#
# 紅隊容器一樣用 `ip netns exec range-redN`（attach-red 建了 /var/run/netns 連結），
# 所以跑的是 **host 的 python3**，容器映像裡有沒有 python 都無所謂。
#
# 每條契約從**對應的區**跑（契約 2 若在 target 跑會連到自己而假通）。任一條破 → exit 1。需 root。
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
VT="$REPO/scripts/verify_topology.py"
MGMT="10.167.10.10"
TARGET="10.167.20.10"
fails=0

has_ns() { ip netns list 2>/dev/null | awk '{print $1}' | grep -qx "$1"; }

# --- 偵測佈局 ---------------------------------------------------------------
if has_ns ns-target; then TARGET_MODE="netns"; else TARGET_MODE="vm"; fi
if has_ns ns-red1; then RED_NS_PREFIX="ns-red"; RED_MODE="netns"
elif has_ns range-red1; then RED_NS_PREFIX="range-red"; RED_MODE="container"
else RED_NS_PREFIX=""; RED_MODE="none"; fi
echo "=== 佈局：靶機=$TARGET_MODE｜紅隊=$RED_MODE ==="

# --- 契約 1：TARGET → MGMT --------------------------------------------------
echo "=== 契約 1：TARGET → MGMT（:3100 :9090 :4317 通）==="
if [ "$TARGET_MODE" = "netns" ]; then
  ip netns exec ns-target python3 "$VT" --from-zone target --mgmt "$MGMT" || fails=1
else
  # 真 VM 無法從 host 直接 exec（刻意：不持有 VM 憑證）。這條在 VM 開機時由
  # build-vm-target 的 cloud-init 自驗過（SLICE2A-RESULT），這裡覆核那份證據。
  if grep -q "SLICE2A-RESULT: PASS" /tmp/range-target-console.log 2>/dev/null; then
    echo "  ✓ 由靶機 VM 開機自驗通過（/tmp/range-target-console.log 的 SLICE2A-RESULT: PASS）"
  else
    echo "  ✗ 找不到靶機 VM 的契約 1 自驗證據"; fails=1
  fi
fi

# --- 契約 2：MGMT → TARGET 反向不通 -----------------------------------------
echo "=== 契約 2：MGMT → TARGET 反向不通（單向；agent pull 的整個理由）==="
ip netns exec ns-mgmt python3 "$VT" --from-zone mgmt --target "$TARGET" || fails=1

# --- 契約 3：RED → MGMT deny all --------------------------------------------
echo "=== 契約 3：RED → MGMT deny all ==="
if [ "$RED_MODE" = "none" ]; then
  echo "  ✗ 找不到紅隊節點（ns-red1 或 range-red1）"; fails=1
else
  ip netns exec "${RED_NS_PREFIX}1" python3 "$VT" --from-zone red --mgmt "$MGMT" || fails=1
fi

# --- 六台 red source IP 可分辨 ----------------------------------------------
echo "=== 六台 red source IP 可分辨（§12.3 red→target:80；防 G0 的 SNAT 塌縮）==="
REC="/tmp/range-target-src.txt"
if [ "$RED_MODE" = "none" ]; then
  echo "  ✗ 無紅隊節點，略過"; fails=1
else
  if [ "$TARGET_MODE" = "netns" ]; then
    # netns 模式：自己在 target 起 stub listener 記錄 source IP。
    : > "$REC"
    ip netns exec ns-target python3 "$DIR/stub_listener.py" --ports 80 --record "$REC" &
    LPID=$!
    sleep 1
  else
    LPID=""   # VM 模式：靶機 app 自己會記 source_ip，稍後從它的 log 取
  fi

  for i in 1 2 3 4 5 6; do
    ip netns exec "${RED_NS_PREFIX}$i" python3 -c \
      "import socket;s=socket.socket();s.settimeout(3);s.connect(('$TARGET',80));s.close()" \
      2>/dev/null || echo "  red$i 連 target:80 失敗（§12.3 應允許）"
  done
  sleep 1
  [ -n "$LPID" ] && { kill "$LPID" 2>/dev/null || true; }

  if [ "$TARGET_MODE" = "vm" ]; then
    # VM 模式：source IP 從**真 Loki** 撈靶機 app 的 log（同時證明 Falco/app 的
    # telemetry 真的走完 TARGET→MGMT→Loki，比 stub listener 更強的證據）。
    : > "$REC"
    LOKI_URL="${PURPLE_LOKI_URL:-http://localhost:3100}"
    for _ in $(seq 1 15); do
      python3 - "$LOKI_URL" "$REC" <<'PY' && break
import json, sys, time, urllib.parse, urllib.request
base, rec = sys.argv[1], sys.argv[2]
end = time.time(); start = end - 300
q = urllib.parse.urlencode({
    "query": '{app="range-target"}', "start": str(int(start*1e9)),
    "end": str(int(end*1e9)), "limit": "1000", "direction": "backward"})
try:
    with urllib.request.urlopen(f"{base.rstrip('/')}/loki/api/v1/query_range?{q}", timeout=5) as r:
        payload = json.load(r)
except Exception:
    sys.exit(1)
ips = []
for stream in (payload.get("data") or {}).get("result") or []:
    for _ts, line in stream.get("values", []):
        try:
            ip = json.loads(line).get("source_ip")
        except Exception:
            ip = None
        if ip:
            ips.append(ip)
if not ips:
    sys.exit(1)
open(rec, "w", encoding="utf-8").write("\n".join(ips) + "\n")
PY
      sleep 2
    done
  fi

  python3 - "$REC" "$REPO/src" <<'PY' || fails=1
import sys
rec, src_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, src_path)
from purple.topology_check import check_source_ips_distinguishable, EXPECTED_KALI
try:
    ips = [ln.strip() for ln in open(rec, encoding="utf-8") if ln.strip()]
except OSError:
    ips = []
print(f"  target 觀測到的 source IP：{sorted(set(ips))}")
msgs = check_source_ips_distinguishable(ips)
if msgs:
    for m in msgs:
        print("  ✗", m)
    sys.exit(1)
print(f"  ✓ {EXPECTED_KALI} 台 red 各自可分辨，未被 SNAT 塌縮")
PY
fi

if [ "$fails" -ne 0 ]; then
  echo "❌ 有契約未通過"
  exit 1
fi
echo "✅ 契約全通過（真方向性防火牆；靶機=$TARGET_MODE、紅隊=$RED_MODE）"
