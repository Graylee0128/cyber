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

BR="br-range"
BASE="10.167.30"
GW="10.167.30.1"
RED_IMAGE="${RED_IMAGE:-nicolaka/netshoot}"
COUNT="${COUNT:-6}"

if ! ovs-vsctl br-exists "$BR" 2>/dev/null; then
  echo "❌ $BR 不存在 —— 先跑 build-range.sh 或 build-vm-target.sh 建四區骨架"
  exit 1
fi

mkdir -p /var/run/netns

echo "▶ 起 $COUNT 台紅隊容器接 $BR / VLAN30（image=$RED_IMAGE）"
for i in $(seq 1 "$COUNT"); do
  name="range-red$i"
  ip="$BASE.1$i"          # 10.167.30.11 .. .16
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
  ovs-vsctl add-port "$BR" "$host_if" tag=30
  ip link set "$host_if" up
  ip link set "$cont_if" netns "$name"
  ip netns exec "$name" ip link set "$cont_if" name eth0
  ip netns exec "$name" ip addr add "$ip/24" dev eth0
  ip netns exec "$name" ip link set eth0 up
  ip netns exec "$name" ip link set lo up
  ip netns exec "$name" ip route add default via "$GW" 2>/dev/null || true
  echo "   • $name → VLAN30 $ip（pid $pid）"
done

echo "✅ $COUNT 台紅隊容器就緒。範例攻擊（六個可分辨 source IP 打靶機 :80）："
echo "   for i in \$(seq 1 $COUNT); do docker exec range-red\$i curl -s -m3 http://10.167.20.10/ >/dev/null && echo red\$i打了; done"
echo "   拆除：scripts/range/teardown-range.sh（已含 range-red* 清理）"
