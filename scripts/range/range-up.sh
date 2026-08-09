#!/usr/bin/env bash
# 票 #13 Slice 4 —— 一鍵起整組 range（IaC）。組合已驗的片段：
#   Slice 1  四區 OVS/VLAN + router + nftables 方向性防火牆（build-range，被 build-vm-target 呼叫）
#   Slice 2a 靶機真 VM 接 VLAN20（build-vm-target）
#   Slice 4  （選）六台紅隊容器接 VLAN30；（選）golden 靶機（Falco+Alloy+app 已烤進）
#
# 選項：
#   --with-red     另起六台紅隊容器接 VLAN30（attach-red.sh），並略過 netns red 免 IP 衝突
#   --with-falco   靶機用 golden image（Falco/Alloy/app 已在內）跑在無網 VLAN20；
#                  golden 不存在會先 build-golden-target.sh 產出；
#                  並把 compose 的真 Loki 掛上 VLAN10（attach-mgmt.sh），讓 VM 推得到
#
# 需 root + Slice 2 依賴。**不在 CI**（巢狀虛擬化）。冪等：內部各步自帶清理。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
# shellcheck source=scripts/range/lib-cloudimg.sh
source "$DIR/lib-cloudimg.sh"
CACHE="/var/lib/libvirt/images"
GOLDEN="$CACHE/range-target-golden.qcow2"

WITH_RED=0
WITH_FALCO=0
for arg in "$@"; do
  case "$arg" in
    --with-red)   WITH_RED=1 ;;
    --with-falco) WITH_FALCO=1 ;;
    *) echo "未知選項：$arg（可用 --with-red / --with-falco）"; exit 2 ;;
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
  echo "▶ 把 compose 的真 Loki 掛上 VLAN10（讓靶機 VM 的 Alloy 推得到＝契約 1 實用）"
  if ! bash "$DIR/attach-mgmt.sh"; then
    echo "   ⚠ Loki 未掛上 VLAN10（compose 起了嗎？）——靶機的 Falco 事件不會進 Loki。"
    echo "     先 docker compose up -d，再單獨跑：sudo bash $DIR/attach-mgmt.sh"
  fi
fi

echo "▶▶ Range up 完成。驗收：sudo bash $DIR/verify-range.sh"
echo "   靶機 VLAN20: 10.167.20.10 | 紅隊 VLAN30: 10.167.30.11~16"
echo "   MGMT VLAN10: 10.167.10.10(stub) / 10.167.10.20(真 Loki)"
