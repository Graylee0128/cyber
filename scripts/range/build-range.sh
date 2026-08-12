#!/usr/bin/env bash
# 票 #13 / WS6 Slice 1 —— 單主機四區 range 骨架（OVS + netns + nftables）。
#
# 用 Open vSwitch 建四區 802.1Q VLAN（SA §12：Z-MGMT10 / Z-TARGET20 / Z-RED30 /
# Z-APP40），節點先用 network namespace（Slice 2 再把靶機/攻擊機換成真 VM）。
# 一個 router netns 做 inter-VLAN 路由，nftables 在它的 forward hook 做**真方向性
# 單向防火牆**——這是 #15 的 docker network membership 逼近做不到、而這裡真做的：
#
#   契約 1  TARGET → MGMT            telemetry :3100/:9090/:4317
#                                        + 指定 receiver :8000 最小權限例外
#   契約 2  MGMT   → TARGET  new     擋（單向；只放 established 回程）
#   契約 3  RED    → MGMT            deny all
#   §12.3   RED    → TARGET :80/:3306 允許（紅隊打靶）
#
# router **不做 SNAT/MASQUERADE** → source IP 保留 → 六台 red 可分辨（防 G0 塌縮）。
#
# 需 root。冪等：先呼叫 teardown。CI 與大主機同一支腳本。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 分區位址的唯一定義處（見 zones.env 開頭）。所有 range 腳本都 source 它，
# 位址不再以字面值散落在各檔。
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
BR="$RANGE_BRIDGE"
BASE="$RANGE_NET_PREFIX"  # <prefix>.<vlan>.<host>
MGMT_CIDR="$RANGE_NET_PREFIX.$Z_MGMT_VLAN.0/24"
TARGET_CIDR="$RANGE_NET_PREFIX.$Z_TARGET_VLAN.0/24"
RED_CIDR="$RANGE_NET_PREFIX.$Z_RED_VLAN.0/24"
RED_HOST_FIRST="${RED_IP_FIRST##*.}"

echo "▶ 清掉可能殘留的舊 range"
# 用 bash 呼叫，不依賴檔案 +x（git checkout 未必帶執行權限，否則 teardown 會被
# permission denied 靜默略過，殘留 br-range 撞下面的 add-br）。
bash "$DIR/teardown-range.sh" >/dev/null 2>&1 || true

echo "▶ 建 OVS bridge $BR"
ovs-vsctl --if-exists del-br "$BR"   # 冪等雙保險：即使上次沒清乾淨也強制重建
ovs-vsctl add-br "$BR"

# add_node <ns> <vlan> <host-octet>
add_node() {
  local ns="$1" vlan="$2" host="$3"
  local ip="$BASE.$vlan.$host"
  ip netns add "$ns"
  ip link add "h-$ns" type veth peer name "c-$ns"
  ip link set "c-$ns" netns "$ns"
  ovs-vsctl add-port "$BR" "h-$ns" tag="$vlan"
  ip link set "h-$ns" up
  ip netns exec "$ns" ip link set lo up
  ip netns exec "$ns" ip link set "c-$ns" name eth0
  ip netns exec "$ns" ip addr add "$ip/24" dev eth0
  ip netns exec "$ns" ip link set eth0 up
  ip netns exec "$ns" ip route add default via "$BASE.$vlan.1"
  echo "   • $ns → VLAN$vlan  $ip"
}

echo "▶ 建 router netns（四區 gateway + inter-VLAN 路由）"
ip netns add ns-router
for vlan in "$Z_MGMT_VLAN" "$Z_TARGET_VLAN" "$Z_RED_VLAN" "$Z_APP_VLAN"; do
  ip link add "h-r$vlan" type veth peer name "c-r$vlan"
  ip link set "c-r$vlan" netns ns-router
  ovs-vsctl add-port "$BR" "h-r$vlan" tag="$vlan"
  ip link set "h-r$vlan" up
  ip netns exec ns-router ip link set "c-r$vlan" name "v$vlan"
  ip netns exec ns-router ip addr add "$BASE.$vlan.1/24" dev "v$vlan"
  ip netns exec ns-router ip link set "v$vlan" up
done
ip netns exec ns-router ip link set lo up
ip netns exec ns-router sysctl -qw net.ipv4.ip_forward=1

echo "▶ 建各區節點"
add_node ns-mgmt "$Z_MGMT_VLAN" "${MGMT_STUB_IP##*.}"
# CI 與 VM 開機自驗都需要一個真的 response receiver endpoint。Slice 4 稍後會由
# attach-mgmt.sh 用同 IP 的真 receiver container 取代這個 stub namespace。
add_node ns-receiver "$Z_MGMT_VLAN" "${MGMT_RECEIVER_IP##*.}"
# 混合模式（Slice 2a）：target 換成真 VM 接 VLAN20，就不建 target netns（IP 會撞）。
if [ "${SKIP_TARGET_NETNS:-0}" = "1" ]; then
  echo "   • ns-target 略過（SKIP_TARGET_NETNS=1；由真 VM 接 VLAN$Z_TARGET_VLAN）"
else
  add_node ns-target "$Z_TARGET_VLAN" "${TARGET_IP##*.}"
fi
# 同理（Slice 4）：紅隊換成真容器接 VLAN30 時，不建 netns red —— 兩者用同一組
# IP（.11~.16），同時存在會在 VLAN30 上位址衝突（ARP 兩邊都回）。
if [ "${SKIP_RED_NETNS:-0}" = "1" ]; then
  echo "   • ns-red1~$RED_COUNT 略過（SKIP_RED_NETNS=1；由真容器接 VLAN$Z_RED_VLAN）"
else
  for i in $(seq 1 "$RED_COUNT"); do
    add_node "ns-red$i" "$Z_RED_VLAN" "$((RED_HOST_FIRST + i - 1))"
  done
fi

echo "▶ 套 nftables 方向性防火牆（在 router 的 forward hook；policy drop）"
# 不引號的 heredoc：要把 zones.env 的網段代進來。nft 規則本身不含 `$`，
# 也不含 `${...}`，所以展開不會誤傷（`{ 80, 3306 }` 在 heredoc 內不做 brace expansion）。
ip netns exec ns-router nft -f - <<NFT
table inet range_fw {
  chain forward {
    type filter hook forward priority filter; policy drop;

    # 回程一律放行（讓契約 1 建立的連線能雙向完成）。
    ct state established,related accept

    # 契約 1：TARGET(VLAN$Z_TARGET_VLAN) → MGMT(VLAN$Z_MGMT_VLAN) 只允許 telemetry。
    ip saddr $TARGET_CIDR ip daddr $MGMT_CIDR tcp dport { 3100, 9090, 4317 } accept

    # Response agent 仍由 TARGET 主動 pull/report，但只能打指定 receiver IP 的 :8000。
    ip saddr $TARGET_CIDR ip daddr $MGMT_RECEIVER_IP tcp dport 8000 accept

    # §12.3：RED(VLAN$Z_RED_VLAN) → TARGET(VLAN$Z_TARGET_VLAN) 的 :80 / :3306 允許（紅隊打靶）。
    ip saddr $RED_CIDR ip daddr $TARGET_CIDR tcp dport { 80, 3306 } accept

    # 其餘全 drop（policy）：
    #   契約 2  MGMT→TARGET 的 new  被擋（沒有放行規則，只有 established 回程）
    #   契約 3  RED→MGMT           被擋
  }
}
NFT

echo "▶ 在 mgmt 起 listeners（telemetry + deny canaries；receiver :8000）"
# nohup + disown：脫離腳本的行程群組，build 腳本 exit 後仍存活給 verify 用。
nohup ip netns exec ns-mgmt python3 "$DIR/stub_listener.py" \
  --ports 3100,9090,4317,22,8000 >/tmp/range-mgmt-listener.log 2>&1 &
echo $! > /tmp/range-mgmt-listener.pid
disown || true
nohup ip netns exec ns-receiver python3 "$DIR/stub_listener.py" \
  --ports 8000 >/tmp/range-receiver-listener.log 2>&1 &
echo $! > /tmp/range-receiver-listener.pid
disown || true
sleep 1  # 讓 telemetry、receiver 與 deny canary 都 bind 完，避免啟動空窗

echo "✅ range 就緒。驗收：scripts/range/verify-range.sh"
