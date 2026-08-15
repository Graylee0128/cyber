#!/usr/bin/env bash
# 票 #13 / #20 —— 單主機六區 range 骨架（OVS + netns + nftables）。
#
# 用 Open vSwitch 建六區 802.1Q VLAN（SA §12：MGMT10 / TARGET20 / RED30 /
# APP40 / EDGE50 / BLUE60），節點先用 network namespace 驗 policy。
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
RED_LATERAL_OVERRIDE="${ALLOW_RED_LATERAL:-}"
BLUE_LATERAL_OVERRIDE="${ALLOW_BLUE_LATERAL:-}"
# 分區位址的唯一定義處（見 zones.env 開頭）。所有 range 腳本都 source 它，
# 位址不再以字面值散落在各檔。
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
[ -n "$RED_LATERAL_OVERRIDE" ] && ALLOW_RED_LATERAL="$RED_LATERAL_OVERRIDE"
[ -n "$BLUE_LATERAL_OVERRIDE" ] && ALLOW_BLUE_LATERAL="$BLUE_LATERAL_OVERRIDE"
BR="$RANGE_BRIDGE"
BASE="$RANGE_NET_PREFIX"  # <prefix>.<vlan>.<host>
MGMT_CIDR="$RANGE_NET_PREFIX.$Z_MGMT_VLAN.0/24"
TARGET_CIDR="$RANGE_NET_PREFIX.$Z_TARGET_VLAN.0/24"
RED_CIDR="$RANGE_NET_PREFIX.$Z_RED_VLAN.0/24"
APP_CIDR="$RANGE_NET_PREFIX.$Z_APP_VLAN.0/24"
EDGE_CIDR="$RANGE_NET_PREFIX.$Z_EDGE_VLAN.0/24"
BLUE_CIDR="$RANGE_NET_PREFIX.$Z_BLUE_VLAN.0/24"
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

echo "▶ 建 router netns（六區 gateway + inter-VLAN 路由）"
ip netns add ns-router
for vlan in "$Z_MGMT_VLAN" "$Z_TARGET_VLAN" "$Z_RED_VLAN" "$Z_APP_VLAN" "$Z_EDGE_VLAN" "$Z_BLUE_VLAN"; do
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
add_node ns-engine "$Z_MGMT_VLAN" "${MGMT_ENGINE_IP##*.}"
add_node ns-app "$Z_APP_VLAN" "${APP_STUB_IP##*.}"
add_node ns-edge "$Z_EDGE_VLAN" "${EDGE_STUB_IP##*.}"
# Two Z-BLUE stubs, not one: a blue seat is the per-player scoring unit
# (#65 decision 25), so "seat A cannot reach seat B" is only provable with a peer.
add_node ns-blue "$Z_BLUE_VLAN" "${BLUE_STUB_IP##*.}"
add_node ns-blue2 "$Z_BLUE_VLAN" "${BLUE_PEER_IP##*.}"
if [ "$ALLOW_BLUE_LATERAL" != "1" ]; then
  ovs-vsctl set port h-ns-blue protected=true
  ovs-vsctl set port h-ns-blue2 protected=true
fi

# Test-only external peer: direct veth, no OVS/internal VLAN membership.
ip netns add ns-internet
ip link add h-internet type veth peer name c-internet
ip link set c-internet netns ns-internet
ip link set h-internet netns ns-router
ip netns exec ns-router ip link set h-internet up
ip netns exec ns-router ip addr add "$INTERNET_TEST_GW/24" dev h-internet
ip netns exec ns-internet ip link set lo up
ip netns exec ns-internet ip link set c-internet name eth0
ip netns exec ns-internet ip addr add "$INTERNET_TEST_IP/24" dev eth0
ip netns exec ns-internet ip link set eth0 up
ip netns exec ns-internet ip route add default via "$INTERNET_TEST_GW"
echo "   • ns-internet -> external test segment $INTERNET_TEST_IP"
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
    if [ "$ALLOW_RED_LATERAL" != "1" ]; then
      ovs-vsctl set port "h-ns-red$i" protected=true
    fi
  done
fi

echo "▶ 套 nftables 方向性防火牆（在 router 的 forward hook；policy drop）"
# 不引號的 heredoc：要把 zones.env 的網段代進來。nft 規則本身不含 `$`，
# 也不含 `${...}`，所以展開不會誤傷（`{ 80, 3306 }` 在 heredoc 內不做 brace expansion）。
ip netns exec ns-router nft -f - <<NFT
table inet range_fw {
  # RED→BLUE DMZ-only allowlist：只放 seat 的 a（DMZ）位址進來，b（內部）
  # 不在集合裡就是被 policy drop 擋住。本檔只宣告空集合——集合成員由 #62
  # seat_provisioner.py 在座位建立/釋放時動態 add/delete element（見該檔
  # _allow_red_to_blue_dmz／_revoke_red_to_blue_dmz），因為「這段裡誰是 a
  # 誰是 b」是 seat runtime 的動態配置，build-range.sh 這層的靜態骨架不知道。
  set blue_dmz_ips {
    type ipv4_addr
  }

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

    # Z-APP managed paths. Raw Loki/query access remains blocked by policy drop.
    ip saddr $RED_CIDR ip daddr $APP_CIDR tcp dport 443 accept
    ip saddr $TARGET_CIDR ip daddr $APP_CIDR tcp dport 443 accept
    ip saddr $APP_CIDR ip daddr $MGMT_ENGINE_IP tcp dport 8001 accept
    ip saddr $MGMT_CIDR ip daddr $APP_CIDR tcp dport 443 accept

    # Contract 5: EDGE has only the proxy paths it needs; no MGMT/TARGET rule.
    ip saddr $INTERNET_TEST_CIDR ip daddr $EDGE_CIDR tcp dport 443 accept
    ip saddr $EDGE_CIDR ip daddr $APP_CIDR tcp dport 443 accept
    ip saddr $EDGE_CIDR ip daddr { $RED_CIDR, $BLUE_CIDR } tcp dport 7681 accept

    # RED → BLUE：只放行到 DMZ 主機 a（見上面 blue_dmz_ips set 的說明）。#65
    # 決策 19 定紅隊主線攻擊面是 Z-BLUE，但正確的規則是「RED → 每段的 DMZ
    # 主機 a」，不是整個 BLUE 網段——開整段等於讓內部主機 b 不必橫向移動就能
    # 直接打，WS8 spec §6.3 的設計就沒了。本檔起手 set 是空的，打不到任何 b；
    # #62 seat_provisioner.py 建座位時才把該座位的 a 位址加進集合。
    ip saddr $RED_CIDR ip daddr @blue_dmz_ips tcp dport 7681 accept

    # 其餘全 drop（policy）：
    #   契約 2  MGMT→TARGET 的 new  被擋（沒有放行規則，只有 established 回程）
    #   契約 3  RED→MGMT           被擋
    #   RED→BLUE 除 a 以外（含 b）  被擋（policy drop，@blue_dmz_ips 沒有的位址一律擋）
  }
}
NFT

echo "▶ 起 policy listeners（所有 deny canary 都真的 listen）"
# nohup + disown：脫離腳本的行程群組，build 腳本 exit 後仍存活給 verify 用。
nohup ip netns exec ns-mgmt python3 "$DIR/stub_listener.py" \
  --ports 3100,9090,4317,22,8000,8001 --http-date-port 3100 \
  >/tmp/range-mgmt-listener.log 2>&1 &
echo $! > /tmp/range-mgmt-listener.pid
disown || true
nohup ip netns exec ns-receiver python3 "$DIR/stub_listener.py" \
  --ports 8000 >/tmp/range-receiver-listener.log 2>&1 &
echo $! > /tmp/range-receiver-listener.pid
disown || true
nohup ip netns exec ns-engine python3 "$DIR/stub_listener.py" --ports 8001 \
  >/tmp/range-engine-listener.log 2>&1 &
echo $! > /tmp/range-engine-listener.pid
disown || true
nohup ip netns exec ns-app python3 "$DIR/stub_listener.py" --ports 443,22 \
  >/tmp/range-app-listener.log 2>&1 &
echo $! > /tmp/range-app-listener.pid
disown || true
nohup ip netns exec ns-edge python3 "$DIR/stub_listener.py" --ports 443,22 \
  >/tmp/range-edge-listener.log 2>&1 &
echo $! > /tmp/range-edge-listener.pid
disown || true
nohup ip netns exec ns-blue python3 "$DIR/stub_listener.py" --ports 7681 \
  >/tmp/range-blue-listener.log 2>&1 &
echo $! > /tmp/range-blue-listener.pid
disown || true
nohup ip netns exec ns-blue2 python3 "$DIR/stub_listener.py" --ports 7681 \
  >/tmp/range-blue2-listener.log 2>&1 &
echo $! > /tmp/range-blue2-listener.pid
disown || true
if [ "${SKIP_RED_NETNS:-0}" != "1" ]; then
  for i in $(seq 1 "$RED_COUNT"); do
    nohup ip netns exec "ns-red$i" python3 "$DIR/stub_listener.py" --ports 7681 \
      >"/tmp/range-red$i-listener.log" 2>&1 &
    disown || true
  done
fi
if [ "${SKIP_TARGET_NETNS:-0}" != "1" ]; then
  : > /tmp/range-target-src.txt
  nohup ip netns exec ns-target python3 "$DIR/stub_listener.py" --ports 80 \
    --record /tmp/range-target-src.txt >/tmp/range-target-listener.log 2>&1 &
  echo $! > /tmp/range-target-listener.pid
  disown || true
fi
sleep 1  # 讓 telemetry、receiver 與 deny canary 都 bind 完，避免啟動空窗

echo "✅ range 就緒。驗收：scripts/range/verify-range.sh"
