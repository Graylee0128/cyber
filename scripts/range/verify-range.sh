#!/usr/bin/env bash
# 票 #13 / WS6 Slice 1 —— 從各區實測四條跨世代契約 + 六台 red source IP 可分辨。
#
# 每條契約從**對應的區** netns 跑（契約 2 若在 target 跑會連到自己而假通）。
# 逐條 echo，方便在 CI log 對照。任一條破 → exit 1。需 root。
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
VT="$REPO/scripts/verify_topology.py"
MGMT="10.167.10.10"
TARGET="10.167.20.10"
fails=0

echo "=== 契約 1：TARGET → MGMT（:3100 :9090 :4317 通）==="
ip netns exec ns-target python3 "$VT" --from-zone target --mgmt "$MGMT" || fails=1

echo "=== 契約 2：MGMT → TARGET 反向不通（單向；agent pull 的整個理由）==="
ip netns exec ns-mgmt python3 "$VT" --from-zone mgmt --target "$TARGET" || fails=1

echo "=== 契約 3：RED → MGMT deny all ==="
ip netns exec ns-red1 python3 "$VT" --from-zone red --mgmt "$MGMT" || fails=1

echo "=== 六台 red source IP 可分辨（§12.3 red→target:80；防 G0 的 SNAT 塌縮）==="
REC="/tmp/range-target-src.txt"
: > "$REC"
ip netns exec ns-target python3 "$DIR/stub_listener.py" --ports 80 --record "$REC" &
LPID=$!
sleep 1
for i in 1 2 3 4 5 6; do
  ip netns exec "ns-red$i" python3 -c \
    "import socket;s=socket.socket();s.settimeout(3);s.connect(('$TARGET',80));s.close()" \
    || echo "  red$i 連 target:80 失敗（§12.3 應允許）"
done
sleep 1
kill "$LPID" 2>/dev/null || true

python3 - "$REC" "$REPO/src" <<'PY' || fails=1
import sys
rec, src_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, src_path)
from purple.topology_check import check_source_ips_distinguishable, EXPECTED_KALI
ips = [ln.strip() for ln in open(rec, encoding="utf-8") if ln.strip()]
print(f"  target 觀測到的 source IP：{sorted(set(ips))}")
msgs = check_source_ips_distinguishable(ips)
if msgs:
    for m in msgs:
        print("  ✗", m)
    sys.exit(1)
print(f"  ✓ {EXPECTED_KALI} 台 red 各自可分辨，未被 SNAT 塌縮")
PY

if [ "$fails" -ne 0 ]; then
  echo "❌ Slice 1 有契約未通過"
  exit 1
fi
echo "✅ Slice 1：四契約 + 六台 source IP 可分辨全通過（真方向性防火牆）"
