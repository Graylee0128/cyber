#!/usr/bin/env bash
# 票 #13 Slice 4 —— 六台紅隊容器接 OVS 的 Z-RED（VLAN30），各自 source IP。
#
# 為什麼六個各自 IP：source IP 可分辨是防 G0 塌縮的硬需求（Slice 1 已在 netns 證過）；
# 這裡把「節點」換成真容器（預設攻擊工具箱 netshoot，可用 RED_IMAGE 換 kali）。
# 接法：容器以 --network none 起，從 host 把 veth 一端塞進容器 netns、另一端掛上
# br-range tag=30（與 build-range 的 add_node 同技術，只是目標是容器的 PID netns）。
#
# 前提：br-range 已由 build-range.sh / build-vm-target.sh 建好。需 root。**不在 CI**。
# 覆寫點：RED_IMAGE（預設 nicolaka/netshoot；真 kali 用 RED_IMAGE=kalilinux/kali-rolling）。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RED_LATERAL_OVERRIDE="${ALLOW_RED_LATERAL:-}"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
[ -n "$RED_LATERAL_OVERRIDE" ] && ALLOW_RED_LATERAL="$RED_LATERAL_OVERRIDE"
BR="$RANGE_BRIDGE"
RED_NET="${RED_IP_FIRST%.*}"        # 10.167.30
RED_HOST_FIRST="${RED_IP_FIRST##*.}"  # 11
GW="$Z_RED_GW"
RED_IMAGE="${RED_IMAGE:-nicolaka/netshoot}"
COUNT="${COUNT:-$RED_COUNT}"

if ! ovs-vsctl br-exists "$BR" 2>/dev/null; then
  echo "❌ $BR 不存在 —— 先跑 build-range.sh 或 build-vm-target.sh 建六區骨架"
  exit 1
fi

mkdir -p /var/run/netns

echo "▶ 起 $COUNT 台紅隊容器接 $BR / VLAN$Z_RED_VLAN（image=$RED_IMAGE）"
for i in $(seq 1 "$COUNT"); do
  name="range-red$i"
  ip="$RED_NET.$((RED_HOST_FIRST + i - 1))"   # zones.env 的 RED_IP_FIRST 起算
  host_if="hr$i"          # host 端 veth（<=15 字元）
  cont_if="cr$i"

  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --network none --cap-add NET_ADMIN \
    "$RED_IMAGE" sleep infinity >/dev/null
  pid="$(docker inspect -f '{{.State.Pid}}' "$name")"
  ln -sf "/proc/$pid/ns/net" "/var/run/netns/$name"

  # veth：一端上 OVS（tag30），一端進容器 netns 當 eth0。
  ip link del "$host_if" 2>/dev/null || true
  ip link add "$host_if" type veth peer name "$cont_if"
  ovs-vsctl --if-exists del-port "$BR" "$host_if"
  ovs-vsctl add-port "$BR" "$host_if" tag="$Z_RED_VLAN"
  if [ "$ALLOW_RED_LATERAL" != "1" ]; then
    ovs-vsctl set port "$host_if" protected=true
  fi
  ip link set "$host_if" up
  ip link set "$cont_if" netns "$name"
  ip netns exec "$name" ip link set "$cont_if" name eth0
  ip netns exec "$name" ip addr add "$ip/24" dev eth0
  ip netns exec "$name" ip link set eth0 up
  ip netns exec "$name" ip link set lo up
  ip netns exec "$name" ip route add default via "$GW" 2>/dev/null || true
  nohup ip netns exec "$name" python3 "$DIR/stub_listener.py" --ports 7681 \
    >"/tmp/range-red$i-listener.log" 2>&1 &
  disown || true
  echo "   • $name → VLAN$Z_RED_VLAN $ip（pid $pid）"
done

# Mirrored host-side guard for Docker-managed seat networks. The current OVS
# veth path is enforced by protected ports above; this rule protects future
# Docker bridge seat attachment without granting containers host policy power.
RED_CIDR="$RANGE_NET_PREFIX.$Z_RED_VLAN.0/24"
if [ "$ALLOW_RED_LATERAL" != "1" ] && iptables -nL DOCKER-USER >/dev/null 2>&1; then
  iptables -C DOCKER-USER -s "$RED_CIDR" -d "$RED_CIDR" \
    -m comment --comment purplescope-red-isolation -j DROP 2>/dev/null || \
  iptables -I DOCKER-USER 1 -s "$RED_CIDR" -d "$RED_CIDR" \
    -m comment --comment purplescope-red-isolation -j DROP
fi

echo "✅ $COUNT 台紅隊容器就緒。範例攻擊（六個可分辨 source IP 打靶機 :80）："
echo "   for i in \$(seq 1 $COUNT); do docker exec range-red\$i curl -s -m3 http://$TARGET_IP/ >/dev/null && echo red\$i打了; done"
echo "   拆除：scripts/range/teardown-range.sh（已含 range-red* 清理）"
