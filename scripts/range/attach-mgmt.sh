#!/usr/bin/env bash
# 票 #9 / #17 / Slice 4 —— 把 compose 的 Loki 與 receiver 掛上 Z-MGMT(VLAN10)。
#
# 為什麼要這步：靶機 VM 在 VLAN20，Loki 在 compose 的 docker 網段，兩者原本不通。
# Loki 讓 VM 內 Alloy 推 telemetry；receiver 讓 target response agent 主動 pull 命令並
# report response Core Event。兩條連線都由 TARGET 發起，MGMT 仍不主動連 target。
# 反向仍被 ns-router 的 nftables 擋（契約 2），RED→MGMT 也照樣 deny（契約 3）。
#
# 技術：與 attach-red 同法，用 /proc/<pid>/ns/net 進容器 netns 掛 veth。
# 關鍵是**用 host 的 ip 指令**操作 —— Loki 映像是 distroless，裡面沒有 shell 也沒有 ip。
#
# 前提：br-range 已建（build-range/build-vm-target）、compose 的 loki 已起。需 root。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
BR="$RANGE_BRIDGE"
GW="$Z_MGMT_GW"
TARGET_CIDR="$RANGE_NET_PREFIX.$Z_TARGET_VLAN.0/24"

if ! ovs-vsctl br-exists "$BR" 2>/dev/null; then
  echo "❌ $BR 不存在 —— 先跑 build-range.sh / range-up.sh"
  exit 1
fi

attach_container() {
  local service="$1" ns="$2" ip="$3" host_if="$4" cont_if="$5" description="$6"
  local cid pid
  cid="$(docker compose -f "$REPO/docker-compose.yml" ps -q "$service" 2>/dev/null || true)"
  if [ -z "$cid" ]; then
    echo "❌ 找不到執行中的 $service 容器 —— 先 docker compose up -d"
    return 1
  fi

  pid="$(docker inspect -f '{{.State.Pid}}' "$cid")"
  mkdir -p /var/run/netns
  ln -sf "/proc/$pid/ns/net" "/var/run/netns/$ns"

  ip link del "$host_if" 2>/dev/null || true
  ovs-vsctl --if-exists del-port "$BR" "$host_if"
  echo "▶ 把 $description 掛上 VLAN$Z_MGMT_VLAN（$ip）"
  ip link add "$host_if" type veth peer name "$cont_if"
  ovs-vsctl add-port "$BR" "$host_if" tag="$Z_MGMT_VLAN"
  ip link set "$host_if" up
  ip link set "$cont_if" netns "$ns"
  ip netns exec "$ns" ip link set "$cont_if" name mgmt0
  ip netns exec "$ns" ip addr add "$ip/24" dev mgmt0
  ip netns exec "$ns" ip link set mgmt0 up
  # 回程必須走 range gateway，否則 container 會把 VLAN20 回包丟去 docker default route。
  ip netns exec "$ns" ip route add "$TARGET_CIDR" via "$GW" dev mgmt0 2>/dev/null || true
}

attach_container loki range-loki "$MGMT_LOKI_IP" hlokimgmt clokimgmt Loki
attach_container receiver range-receiver "$MGMT_RECEIVER_IP" hrecvmgmt crecvmgmt receiver

echo "✅ Z-MGMT 接線完成：Loki $MGMT_LOKI_IP:3100；response pull/report $MGMT_RECEIVER_IP:8000"
