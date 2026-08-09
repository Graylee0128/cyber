#!/usr/bin/env bash
# PurpleScope —— 一鍵部署。這是**唯一**你需要記得的部署指令，其餘 .sh 由它呼叫。
#
#   sudo bash deploy.sh              # 自動判斷環境，部署到最完整的形態
#   sudo bash deploy.sh --stack-only # 只起觀測棧（compose），不建 range（免巢狀虛擬化）
#   sudo bash deploy.sh --reset      # 先拆乾淨再部署（演練之間回到已知狀態）
#
# 它做兩層：
#   L1 觀測／評估平面（compose）：Postgres / Loki / Prometheus / Grafana / Alloy /
#      receiver / evaluation-engine —— 一律要起。
#   L2 Range（單主機巢狀）：四區 OVS VLAN + nftables 方向性防火牆 + 靶機真 VM +
#      六台紅隊容器 —— 需 KVM/libvirt/OVS，沒有就自動略過並說明。
#
# Falco 兩案自動選（見 scripts/range/falco-mode.sh）：
#   container  舊方案：Falco 跑 compose 容器（吃 host kernel）
#   vm         新方案：Falco 跑 golden 靶機 VM(kernel 6.8)，繞開 host kernel 限制
# 覆寫：FALCO_MODE=container|vm bash deploy.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RANGE="$REPO/scripts/range"
STACK_ONLY=0
RESET=0

for arg in "$@"; do
  case "$arg" in
    --stack-only) STACK_ONLY=1 ;;
    --reset)      RESET=1 ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知選項：$arg（--stack-only / --reset）"; exit 2 ;;
  esac
done

FALCO_MODE="$(bash "$RANGE/falco-mode.sh")"
echo "════════════════════════════════════════════════════════════"
echo " PurpleScope 部署   Falco 模式：$FALCO_MODE   (kernel $(uname -r))"
echo "════════════════════════════════════════════════════════════"

# --- 前置：能不能建 range？（缺工具就只起 compose，不假裝成功）----------------
CAN_RANGE=1
for tool in ovs-vsctl virsh virt-install qemu-img; do
  command -v "$tool" >/dev/null 2>&1 || { echo "⚠ 缺 $tool"; CAN_RANGE=0; }
done
[ -e /dev/kvm ] || { echo "⚠ 無 /dev/kvm（不支援巢狀虛擬化）"; CAN_RANGE=0; }
if [ "$CAN_RANGE" = 0 ]; then
  echo "→ 只部署觀測棧。要完整 range 請照 scripts/range/HOST-SETUP.md 裝依賴。"
  STACK_ONLY=1
fi

if [ "$RESET" = 1 ]; then
  echo "▶ [reset] 拆除舊 range 與舊 compose"
  bash "$RANGE/teardown-range.sh" || true
  docker compose -f "$REPO/docker-compose.yml" --profile falco down -v 2>/dev/null || true
fi

# --- L1 觀測／評估平面（compose）--------------------------------------------
echo
echo "▶▶ L1 觀測平面（docker compose）"
COMPOSE_ARGS=()
if [ "$FALCO_MODE" = "container" ]; then
  echo "   Falco 走容器（--profile falco）"
  COMPOSE_ARGS+=(--profile falco)
else
  echo "   Falco 不走容器（host kernel 不支援）；改由 range 的 golden 靶機 VM 承載"
fi
docker compose -f "$REPO/docker-compose.yml" "${COMPOSE_ARGS[@]}" \
  up -d --build --wait --wait-timeout 240
docker compose -f "$REPO/docker-compose.yml" "${COMPOSE_ARGS[@]}" ps

if [ "$STACK_ONLY" = 1 ]; then
  echo
  echo "✅ 部署完成（僅觀測平面）。測試：bash test.sh"
  echo "   Grafana http://localhost:3000 (admin/admin) | Loki :3100 | Evidence API :8001"
  exit 0
fi

# --- L2 Range（四區 + 靶機 VM + 紅隊）---------------------------------------
echo
echo "▶▶ L2 Range（四區 VLAN + 靶機真 VM + 六台紅隊容器）"
UP_ARGS=(--with-red)
if [ "$FALCO_MODE" = "vm" ]; then
  # golden 靶機（Falco+Alloy+app 已烤進）＋ 把真 Loki 掛上 VLAN10 讓 VM 推得到
  UP_ARGS+=(--with-falco)
fi
bash "$RANGE/range-up.sh" "${UP_ARGS[@]}"

echo
echo "✅ 部署完成。測試：sudo bash test.sh"
echo "   Grafana http://localhost:3000 (admin/admin) | Loki :3100 | Evidence API :8001"
echo "   靶機 VM 10.167.20.10 | 紅隊 10.167.30.11~16 | MGMT 10.167.10.10 / Loki .20"
