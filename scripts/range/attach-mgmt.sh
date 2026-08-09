#!/usr/bin/env bash
# 票 #9 / Slice 4 —— 把 compose 的**真 Loki** 掛上 Z-MGMT(VLAN10)，位址 10.167.10.20。
#
# 為什麼要這步：靶機 VM 在 VLAN20，Loki 在 compose 的 docker 網段，兩者原本不通。
# 把 Loki 接上 VLAN10（它本來就是 SA §12 的 MGMT 住戶），VM 內的 Alloy 就能推
# `TARGET → MGMT :3100` —— 那正是**契約 1 的實用**，不是模擬。
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
NS="range-loki"
IP="$MGMT_LOKI_IP"
GW="$Z_MGMT_GW"
TARGET_CIDR="$RANGE_NET_PREFIX.$Z_TARGET_VLAN.0/24"
HOST_IF="hlokimgmt"
CONT_IF="clokimgmt"

if ! ovs-vsctl br-exists "$BR" 2>/dev/null; then
  echo "❌ $BR 不存在 —— 先跑 build-range.sh / range-up.sh"
  exit 1
fi

CID="$(docker compose -f "$REPO/docker-compose.yml" ps -q loki 2>/dev/null || true)"
if [ -z "$CID" ]; then
  echo "❌ 找不到執行中的 loki 容器 —— 先 docker compose up -d"
  exit 1
fi

PID="$(docker inspect -f '{{.State.Pid}}' "$CID")"
mkdir -p /var/run/netns
ln -sf "/proc/$PID/ns/net" "/var/run/netns/$NS"

# 冪等：已掛過就先拆。
ip link del "$HOST_IF" 2>/dev/null || true
ovs-vsctl --if-exists del-port "$BR" "$HOST_IF"

echo "▶ 把 Loki 掛上 VLAN10（$IP）"
ip link add "$HOST_IF" type veth peer name "$CONT_IF"
ovs-vsctl add-port "$BR" "$HOST_IF" tag=10
ip link set "$HOST_IF" up
ip link set "$CONT_IF" netns "$NS"
ip netns exec "$NS" ip link set "$CONT_IF" name mgmt0
ip netns exec "$NS" ip addr add "$IP/24" dev mgmt0
ip netns exec "$NS" ip link set mgmt0 up
# 回程路由：Loki 回給 VLAN20 的封包要走 range gateway，否則會被丟去 docker 預設路由。
ip netns exec "$NS" ip route add "$TARGET_CIDR" via "$GW" dev mgmt0 2>/dev/null || true

echo "✅ Loki 已在 Z-MGMT：$IP:3100（靶機 VM 的 Alloy 推這裡＝契約 1 實用）"
