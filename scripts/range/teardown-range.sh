#!/usr/bin/env bash
# 票 #13 / #20 —— 拆掉六區 range。冪等、可重複跑（Reset 的雛形）。
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
RED_CIDR="$RANGE_NET_PREFIX.$Z_RED_VLAN.0/24"

pkill -f "scripts/range/stub_listener.py" 2>/dev/null || true
pkill -f "scripts/range/seat_provisioner.py" 2>/dev/null || true
rm -f /tmp/range-seat-provisioner.pid

# Slice 4：先清紅隊容器（含其 netns 符號連結）。
if command -v docker >/dev/null 2>&1; then
  for i in 1 2 3 4 5 6; do
    docker rm -f "range-red$i" 2>/dev/null || true
    rm -f "/var/run/netns/range-red$i" 2>/dev/null || true
  done
  # Seat Provisioner（#62）動態建的座位容器——跟上面 range-red* 是不同命名
  # 空間（seat_provisioner.py 的 sweep_orphans() 特意不碰 range-red*），
  # provisioner 已被 pkill，這裡不會有 admission API 可查，直接全清。
  for name in $(docker ps -a --filter "name=seat-red-" --filter "name=seat-blue-" --format "{{.Names}}" 2>/dev/null); do
    docker rm -f "$name" 2>/dev/null || true
    rm -f "/var/run/netns/$name" 2>/dev/null || true
  done
fi
# Loki / receiver 掛在 VLAN10 的 veth（attach-mgmt）。容器本身不動。
ip link del hlokimgmt 2>/dev/null || true
ip link del hrecvmgmt 2>/dev/null || true
rm -f /var/run/netns/range-loki /var/run/netns/range-receiver 2>/dev/null || true

# Slice 2a/2b/4：關 VM 與 libvirt network（若有裝 libvirt）。
if command -v virsh >/dev/null 2>&1; then
  for vm in range-target range-falco-smoke range-golden-build; do
    virsh destroy "$vm" 2>/dev/null || true
    virsh undefine "$vm" --nvram 2>/dev/null || true
  done
  virsh net-destroy range-ovs 2>/dev/null || true
  virsh net-undefine range-ovs 2>/dev/null || true
fi

if command -v iptables >/dev/null 2>&1; then
  while iptables -D DOCKER-USER -s "$RED_CIDR" -d "$RED_CIDR" \
    -m comment --comment purplescope-red-isolation -j DROP 2>/dev/null; do :; done
fi

for ns in ns-mgmt ns-receiver ns-engine ns-app ns-edge ns-blue ns-blue2 ns-internet ns-target ns-red1 ns-red2 ns-red3 ns-red4 ns-red5 ns-red6 ns-router; do
  ip netns del "$ns" 2>/dev/null || true
done

# 刪 bridge 一次清掉所有 OVS port（veth 隨 netns 消失）。
ovs-vsctl --if-exists del-br br-range 2>/dev/null || true

rm -f /tmp/range-*.pid /tmp/range-target-src.txt 2>/dev/null || true
echo "range 已拆除"
