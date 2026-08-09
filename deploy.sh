#!/usr/bin/env bash
# PurpleScope —— 一鍵部署。這是**唯一**你需要記得的部署指令，其餘 .sh 由它呼叫。
#
#   sudo bash deploy.sh                # 自動判斷環境，部署到最完整的形態
#   sudo bash deploy.sh --install-deps # 乾淨主機：連依賴一起裝（docker/OVS/libvirt/…）
#   sudo bash deploy.sh --stack-only   # 只起觀測棧（compose），不建 range
#   sudo bash deploy.sh --reset        # 先拆乾淨再部署（演練之間回到已知狀態）
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
INSTALL_DEPS=0

for arg in "$@"; do
  case "$arg" in
    --stack-only)  STACK_ONLY=1 ;;
    --reset)       RESET=1 ;;
    --install-deps) INSTALL_DEPS=1 ;;
    -h|--help)     sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "未知選項：$arg（--install-deps / --stack-only / --reset）"; exit 2 ;;
  esac
done

[ "$(id -u)" = 0 ] || { echo "❌ 需要 root：sudo bash deploy.sh $*"; exit 1; }

FALCO_MODE="$(bash "$RANGE/falco-mode.sh")"
echo "════════════════════════════════════════════════════════════"
echo " PurpleScope 部署   Falco 模式：$FALCO_MODE   (kernel $(uname -r))"
echo "════════════════════════════════════════════════════════════"

# --- Preflight：這台機器能做到哪一層？（乾淨主機也能照做）--------------------
#
# 分兩級：docker 是 L1 硬依賴（沒有就什麼都跑不了，直接失敗）；
# 虛擬化/OVS 是 L2 依賴（沒有就退成 --stack-only，說明原因，不假裝成功）。
# 「指令存在」不等於「服務在跑」—— daemon 沒起照樣會在建 range 時炸，所以一併檢查。

apt_install() {  # apt_install <套件...>
  echo "   apt-get install: $*"
  DEBIAN_FRONTEND=noninteractive apt-get update -q
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@"
}

ensure_service() {  # ensure_service <unit>；已跑就跳過，沒跑就試著起
  systemctl is-active --quiet "$1" && return 0
  echo "   啟動服務 $1"
  systemctl enable --now "$1" >/dev/null 2>&1 || return 1
  systemctl is-active --quiet "$1"
}

echo
echo "▶▶ Preflight（環境檢查）"

# L1：docker —— 沒有它連觀測棧都起不了。
if ! command -v docker >/dev/null 2>&1; then
  if [ "$INSTALL_DEPS" = 1 ]; then
    echo "⚠ 缺 docker，安裝中"
    apt_install docker.io docker-compose-v2
  else
    echo "❌ 缺 docker（L1 硬依賴）。裝法：sudo bash deploy.sh --install-deps"
    echo "   或手動：sudo apt-get install -y docker.io docker-compose-v2"
    exit 1
  fi
fi
ensure_service docker || { echo "❌ docker 服務起不來"; exit 1; }
docker compose version >/dev/null 2>&1 || {
  echo "❌ 沒有 docker compose v2 外掛（試 sudo apt-get install -y docker-compose-v2）"; exit 1; }
echo "   ✓ docker $(docker --version | awk '{print $3}' | tr -d ,)"

# L2：虛擬化 + OVS + nftables —— 建 range 用。
RANGE_PKGS=(openvswitch-switch libvirt-daemon-system qemu-kvm virtinst nftables cpu-checker)
MISSING=()
for tool in ovs-vsctl virsh virt-install qemu-img nft; do
  command -v "$tool" >/dev/null 2>&1 || MISSING+=("$tool")
done
if [ ${#MISSING[@]} -gt 0 ] && [ "$INSTALL_DEPS" = 1 ]; then
  echo "⚠ 缺 ${MISSING[*]}，安裝中（約 1–3 分鐘）"
  apt_install "${RANGE_PKGS[@]}"
  MISSING=()
  for tool in ovs-vsctl virsh virt-install qemu-img nft; do
    command -v "$tool" >/dev/null 2>&1 || MISSING+=("$tool")
  done
fi

CAN_RANGE=1
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "⚠ 缺 ${MISSING[*]}"
  CAN_RANGE=0
else
  # 服務要真的在跑（OVS 先於 libvirt —— VM 網路依賴 bridge 先在）。
  ensure_service openvswitch-switch || { echo "⚠ openvswitch-switch 起不來"; CAN_RANGE=0; }
  ensure_service libvirtd          || { echo "⚠ libvirtd 起不來"; CAN_RANGE=0; }
fi

# 硬體虛擬化：靶機是真 VM，沒有 /dev/kvm 就只能軟體模擬（慢到不實用），視為不可建。
if [ ! -e /dev/kvm ]; then
  echo "⚠ 無 /dev/kvm —— CPU 虛擬化沒開（BIOS 的 VT-x/AMD-V），或本機是未開 nested 的 VM"
  CAN_RANGE=0
else
  echo "   ✓ /dev/kvm 存在"
fi

# 資訊性：本機若是 VM，要靠 nested；是實體機則無所謂。有 kernel BTF 才可能跑容器 Falco。
NESTED="$(cat /sys/module/kvm_amd/parameters/nested /sys/module/kvm_intel/parameters/nested 2>/dev/null | head -1 || true)"
# 同上：實體機沒有 kvm_*/nested 這些檔，`[ -n "" ] && echo` 會回非 0 而讓 set -e 中止。
[ -n "$NESTED" ] && echo "   · nested 虛擬化：$NESTED（本機若是實體機可忽略）" || true
[ -e /sys/kernel/btf/vmlinux ] && echo "   · kernel BTF：有" || echo "   · kernel BTF：無（容器 Falco 不可用）"

# python + pytest：測試層要用。在部署階段就把 repo 內的 .venv 準備好，
# 之後 `sudo bash test.sh` 不會再倒在 root 看不到 pytest 上（實測 2026-08-09）。
# 這裡失敗不擋部署 —— 部署不需要 pytest，只是先講清楚測試會受影響。
# shellcheck source=scripts/lib-python.sh
source "$REPO/scripts/lib-python.sh"
if PY_FOR_TESTS="$(purple_pick_python "$REPO")"; then
  echo "   ✓ 測試用 python：$PY_FOR_TESTS"
else
  echo "⚠ 備不出帶 pytest 的 python（缺 python3-venv？）—— test.sh 的 T1/T2/T4 會略過"
  # 結尾一定要 `|| true`：`set -e` 下 `[ ] && { }` 條件不成立會讓整個 else 分支回非 0，
  # 進而讓整支腳本在這裡無聲中止。
  if [ "$INSTALL_DEPS" = 1 ]; then
    apt_install python3-venv
    purple_pick_python "$REPO" >/dev/null || true
  fi
fi

if [ "$CAN_RANGE" = 0 ]; then
  echo "→ 只部署觀測棧（L1）。要完整 range：sudo bash deploy.sh --install-deps"
  echo "   細節見 scripts/range/HOST-SETUP.md"
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
