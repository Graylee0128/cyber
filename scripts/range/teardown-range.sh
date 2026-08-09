#!/usr/bin/env bash
# 票 #13 / WS6 Slice 1 —— 拆掉四區 range。冪等、可重複跑（Reset 的雛形）。
set -uo pipefail

pkill -f "scripts/range/stub_listener.py" 2>/dev/null || true

# Slice 2a：先關 VM 與 libvirt network（若有裝 libvirt）。
if command -v virsh >/dev/null 2>&1; then
  virsh destroy range-target 2>/dev/null || true
  virsh undefine range-target --nvram 2>/dev/null || true
  virsh net-destroy range-ovs 2>/dev/null || true
  virsh net-undefine range-ovs 2>/dev/null || true
fi

for ns in ns-mgmt ns-target ns-red1 ns-red2 ns-red3 ns-red4 ns-red5 ns-red6 ns-router; do
  ip netns del "$ns" 2>/dev/null || true
done

# 刪 bridge 一次清掉所有 OVS port（veth 隨 netns 消失）。
ovs-vsctl --if-exists del-br br-range 2>/dev/null || true

rm -f /tmp/range-*.pid /tmp/range-target-src.txt 2>/dev/null || true
echo "range 已拆除"
