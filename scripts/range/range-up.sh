#!/usr/bin/env bash
# 票 #13 Slice 4 —— 一鍵起整組 range（IaC）。組合已驗的片段：
#   Slice 1  四區 OVS/VLAN + router + nftables 方向性防火牆（build-range，被 build-vm-target 呼叫）
#   Slice 2a 靶機真 VM 接 VLAN20（build-vm-target）
#   Slice 4  （選）六台紅隊容器接 VLAN30；（選）golden 靶機（Falco+Alloy+app 已烤進）
#
# 選項：
#   --with-red     另起六台紅隊容器接 VLAN30（attach-red.sh），並略過 netns red 免 IP 衝突
#   --with-falco   （預設已開，此旗標僅相容舊呼叫，無作用）
#   --no-falco     靶機改用乾淨 cloud image（無 Falco/Alloy/app/mariadb）跑在無網 VLAN20；
#                  只在需要純網路骨架、不需要真攻擊面時使用（例：只測拓樸契約）
#
# 2026-08-14：golden（Falco/Alloy/app 已烤進）改為預設，不再要求呼叫端記得加旗標——
# 沒有真攻擊面的「靶機」對這個平台的用途來說不是合理的預設狀態（見 #44）。
#
# 需 root + Slice 2 依賴。**不在 CI**（巢狀虛擬化）。冪等：內部各步自帶清理。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
# shellcheck source=scripts/range/lib-cloudimg.sh
source "$DIR/lib-cloudimg.sh"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
CACHE="/var/lib/libvirt/images"
GOLDEN="$CACHE/range-target-golden.qcow2"

WITH_RED=0
WITH_FALCO=1
for arg in "$@"; do
  case "$arg" in
    --with-red)   WITH_RED=1 ;;
    --with-falco) WITH_FALCO=1 ;;
    --no-falco)   WITH_FALCO=0 ;;
    *) echo "未知選項：$arg（可用 --with-red / --no-falco）"; exit 2 ;;
  esac
done

echo "▶▶ Range up 開始（with-red=$WITH_RED with-falco=$WITH_FALCO）"

# 紅隊用真容器時，骨架就別建 netns red（同 IP 會衝突）。
export SKIP_RED_NETNS="$WITH_RED"

if [ "$WITH_FALCO" = 1 ]; then
  # 比對內容指紋：不只看 golden 在不在，還要確認它是用**現在這版**來源檔烤的。
  # 否則改了靶機 app / Alloy 設定卻沿用舊 golden，會得到「檔案在但功能不對」的假象。
  WANT="$(golden_stamp "$REPO")"
  HAVE="$(cat "$GOLDEN.stamp" 2>/dev/null || echo none)"
  if [ ! -f "$GOLDEN" ]; then
    echo "▶ golden image 不存在，先烤（build-golden-target.sh，約 4–8 分鐘）"
    bash "$DIR/build-golden-target.sh"
  elif [ "$WANT" != "$HAVE" ]; then
    echo "▶ golden image 已過時（來源檔指紋不符：have=${HAVE:0:12} want=${WANT:0:12}），重烤"
    bash "$DIR/build-golden-target.sh"
  fi
  echo "▶ 靶機用 golden image 起在 VLAN20（Falco/Alloy/app 已在內）"
  BASE_OVERRIDE="$GOLDEN" bash "$DIR/build-vm-target.sh"
else
  echo "▶ 靶機用乾淨 cloud image 起在 VLAN20"
  bash "$DIR/build-vm-target.sh"
fi

if [ "$WITH_RED" = 1 ]; then
  echo "▶ 起六台紅隊容器接 VLAN30"
  bash "$DIR/attach-red.sh"
fi

if [ "$WITH_FALCO" = 1 ]; then
  echo "▶ 把 compose 的 Loki／receiver 掛上 VLAN10（telemetry push ＋ response pull/report）"
  if ! bash "$DIR/attach-mgmt.sh"; then
    echo "   ⚠ MGMT 住戶未完整掛上 VLAN10（compose 起了嗎？）——telemetry／response 會不完整。"
    echo "     先 docker compose up -d，再單獨跑：sudo bash $DIR/attach-mgmt.sh"
  fi
fi

# admission-edge（玩家 ttyd 代理）：跟上面 attach-mgmt 一樣是 best-effort——
# 只有起了 admission-e2e profile（有 admission-edge 容器）才有事可做，沒起就
# 跳過，不擋 range-up。沒接這步的後果：玩家 Portal 的 Shell 面板打 /terminal/
# 全部 502（admission-edge 連不到紅／藍隊容器，見 attach-edge.sh 開頭）。
echo "▶ 把 compose 的 admission-edge 掛上 VLAN50（玩家 ttyd 代理走 Z-EDGE）"
if ! bash "$DIR/attach-edge.sh"; then
  echo "   ⚠ admission-edge 未掛上 VLAN50（admission-e2e profile 起了嗎？）——玩家 Shell 面板會打不通。"
  echo "     先 docker compose --profile admission-e2e up -d，再單獨跑：sudo bash $DIR/attach-edge.sh"
fi

# Seat Provisioner Agent（#62 補接線）：host 側常駐，把 admission 的
# requested 座位建成真容器。跟上面 attach-mgmt 一樣是 best-effort——
# admission 沒起就跳過，不擋 range-up。
echo "▶ 啟動 Seat Provisioner Agent（host 側常駐，輪詢 admission 待建座位）"
bash "$DIR/seat-provisioner-daemon.sh" start

echo "▶▶ Range up 完成。驗收：sudo bash $DIR/verify-range.sh"
echo "   靶機 VLAN$Z_TARGET_VLAN: $TARGET_IP | 紅隊 VLAN$Z_RED_VLAN: $RED_IP_FIRST 起 $RED_COUNT 台"
echo "   MGMT VLAN$Z_MGMT_VLAN: $MGMT_STUB_IP(stub) / $MGMT_LOKI_IP(Loki) / $MGMT_RECEIVER_IP(receiver)"
