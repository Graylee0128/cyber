#!/usr/bin/env bash
# 票 #13 Slice 4 —— 六台紅隊容器接 OVS 的 Z-RED（VLAN30），各自 source IP。
#
# 為什麼六個各自 IP：source IP 可分辨是防 G0 塌縮的硬需求（Slice 1 已在 netns 證過）；
# 這裡把「節點」換成真容器（預設攻擊工具箱 netshoot，可用 RED_IMAGE 換 kali）。
# 接法：容器以 --network none 起，從 host 把 veth 一端塞進容器 netns、另一端掛上
# br-range tag=30（與 build-range 的 add_node 同技術，只是目標是容器的 PID netns）。
#
# 前提：br-range 已由 build-range.sh / build-vm-target.sh 建好。需 root。**不在 CI**。
# 覆寫點：RED_IMAGE（預設 purplescope/red-attacker，本地 build；票 #46）。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
RED_LATERAL_OVERRIDE="${ALLOW_RED_LATERAL:-}"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
[ -n "$RED_LATERAL_OVERRIDE" ] && ALLOW_RED_LATERAL="$RED_LATERAL_OVERRIDE"
BR="$RANGE_BRIDGE"
RED_NET="${RED_IP_FIRST%.*}"        # 10.167.30
RED_HOST_FIRST="${RED_IP_FIRST##*.}"  # 11
GW="$Z_RED_GW"
# 票 #46：預設用本地烤的最小攻擊 image（curl + mariadb-client），內容由 #44 攻擊鏈決定。
# netshoot 沒有 SQL client，第二跳（直連 :3306）打不動 —— 見 deploy/red-attacker/Dockerfile。
RED_IMAGE="${RED_IMAGE:-purplescope/red-attacker:latest}"
COUNT="${COUNT:-$RED_COUNT}"

# image 不存在就在 host（有網）先 build 好 —— 容器接上無網的 VLAN30 後才不需要任何安裝步驟。
# 只對預設 image 自動 build；使用者若指定別的 RED_IMAGE，由他自己負責先拉/建好。
if [ "$RED_IMAGE" = "purplescope/red-attacker:latest" ] && ! docker image inspect "$RED_IMAGE" >/dev/null 2>&1; then
  echo "▶ 本地 build 紅隊 image（$RED_IMAGE，host 有網，約 30–60s）"
  docker build -t "$RED_IMAGE" "$REPO/deploy/red-attacker"
fi

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

# Host-side guard for the RED containers **as they are attached today**: they sit
# on $RED_CIDR, so this rule blocks red-to-red once traffic ever reaches the
# Docker forward path instead of the OVS veth path (which protected ports above
# already cover). It is deliberately narrow.
#
# 它**不**覆蓋未來的 per-seat 容器網路：WS8 spec §6.4 的 Z-BLUE 隔離是「每個 seat 一個
# 獨立容器網路（172.31.<X>.0/24）＋ DOCKER-USER DROP」，那些網段不在 $RED_CIDR 裡，
# 這條規則一次都不會命中，而且它們的 veth 也不掛在 br-range 上（protected ports 同樣管不到）。
# 那一層屬 #62 seat runtime，不要以為這裡已經保護到了。
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
