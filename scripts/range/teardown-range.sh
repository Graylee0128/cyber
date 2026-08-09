#!/usr/bin/env bash
# 票 #13 / WS6 Slice 1 —— 拆掉四區 range。冪等、可重複跑（Reset 的雛形）。
set -uo pipefail

pkill -f "scripts/range/stub_listener.py" 2>/dev/null || true

for ns in ns-mgmt ns-target ns-red1 ns-red2 ns-red3 ns-red4 ns-red5 ns-red6 ns-router; do
  ip netns del "$ns" 2>/dev/null || true
done

# 刪 bridge 一次清掉所有 OVS port（veth 隨 netns 消失）。
ovs-vsctl --if-exists del-br br-range 2>/dev/null || true

rm -f /tmp/range-*.pid /tmp/range-target-src.txt 2>/dev/null || true
echo "range 已拆除"
