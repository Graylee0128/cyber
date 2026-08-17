#!/usr/bin/env bash
# 把 compose 的 admission-edge 掛上 Z-EDGE(VLAN50)。
#
# 為什麼要這步：build-range.sh 的 nftables 已經放行 EDGE_CIDR → {RED_CIDR,
# BLUE_CIDR} tcp:7681（見該檔 Contract 5：「EDGE has only the proxy paths it
# needs」）——這條規則從一開始就是為 admission-edge 這個 ttyd 代理寫的。但
# admission-edge 在 docker-compose.yml 裡原本只掛在 Docker 自己建的 z-red／
# z-blue 這兩個**同名不同物**的橋接網路上：z-red／z-blue 的 IPAM 子網刻意設成
# 跟 OVS 的 Z-RED／Z-BLUE 一樣（10.167.30.0/24／10.167.60.0/24），但那只是
# Docker 自己起的獨立 bridge，從來沒有真的接上 br-range——admission-edge 對
# 紅／藍隊容器的連線因此一律 "Host is unreachable"（pre-UAT 2026-08-17 發現：
# 玩家 Portal 的 Shell 面板在真 scenario 上從未真的打通過）。
#
# 修法跟 attach-mgmt.sh 對 Loki／receiver 做的事完全同款：把 admission-edge
# 用 veth 真的插進 br-range 的 VLAN50，位址沿用 build-range.sh 已經建好、驗過
# policy 的 ns-edge stub 位址（EDGE_STUB_IP）——接上真容器前先拆 stub，避免
# ARP 衝突。不動容器原本經 Docker z-app 網路打 admission:8000 的預設路由，
# 只**額外**幫 RED_CIDR／BLUE_CIDR 加路由經這條新 veth：兩件事各管各的，
# admission:8000（auth_request）繼续走 Docker DNS，紅／藍隊 ttyd 才走 OVS。
#
# 前提：br-range 已建（build-range/range-up）、compose 的 admission-edge 已起。
# docker-compose.yml 需先把 admission-edge 從 z-red／z-blue 兩個網路移除
# （否則 Docker 幫那兩個子網裝的 connected route 會跟這裡加的路由打架）。需 root。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
BR="$RANGE_BRIDGE"
GW="$Z_EDGE_GW"
EDGE_IP="$EDGE_STUB_IP"
RED_CIDR="$RANGE_NET_PREFIX.$Z_RED_VLAN.0/24"
BLUE_CIDR="$RANGE_NET_PREFIX.$Z_BLUE_VLAN.0/24"

if ! ovs-vsctl br-exists "$BR" 2>/dev/null; then
  echo "❌ $BR 不存在 —— 先跑 build-range.sh / range-up.sh"
  exit 1
fi

# build-range 的 ns-edge 只供 CI / policy 自驗用的 stub。改掛真 admission-edge
# 容器前先移除，避免同一個 EDGE_IP 在 VLAN50 發生 ARP 衝突（同 attach-mgmt.sh
# 對 ns-receiver 的處理）。
if ip netns list 2>/dev/null | awk '{print $1}' | grep -qx ns-edge; then
  ip netns del ns-edge
  ovs-vsctl --if-exists del-port "$BR" h-ns-edge
fi

cid="$(docker compose -f "$REPO/docker-compose.yml" ps -q admission-edge 2>/dev/null || true)"
if [ -z "$cid" ]; then
  echo "❌ 找不到執行中的 admission-edge 容器 —— 先 docker compose up -d admission-edge"
  exit 1
fi

pid="$(docker inspect -f '{{.State.Pid}}' "$cid")"
mkdir -p /var/run/netns
ln -sf "/proc/$pid/ns/net" "/var/run/netns/range-edge"

ip link del h-edgemgmt 2>/dev/null || true
ovs-vsctl --if-exists del-port "$BR" h-edgemgmt
echo "▶ 把 admission-edge 掛上 VLAN$Z_EDGE_VLAN（$EDGE_IP）"
ip link add h-edgemgmt type veth peer name c-edgemgmt
ovs-vsctl add-port "$BR" h-edgemgmt tag="$Z_EDGE_VLAN"
ip link set h-edgemgmt up
ip link set c-edgemgmt netns range-edge
ip netns exec range-edge ip link set c-edgemgmt name edge0
ip netns exec range-edge ip addr add "$EDGE_IP/24" dev edge0
ip netns exec range-edge ip link set edge0 up

# 只加這兩條——不動容器原本經 Docker z-app 網路打 admission:8000 的預設路由。
ip netns exec range-edge ip route add "$RED_CIDR" via "$GW" dev edge0 2>/dev/null || true
ip netns exec range-edge ip route add "$BLUE_CIDR" via "$GW" dev edge0 2>/dev/null || true

echo "✅ Z-EDGE 接線完成：admission-edge $EDGE_IP，可達 RED $RED_CIDR／BLUE $BLUE_CIDR :7681"
