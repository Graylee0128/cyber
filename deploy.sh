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
#
# AI 輔助（#131，Ollama + qwen2.5:3b）：磁碟空間夠（模型檔約 2GB）就自動開，
# 不夠或模型拉取失敗只印警告、不阻斷部署——選配加分項，不是硬依賴。
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

# --- Phase timing（#144 D1）---------------------------------------------------
#
# 非開發者看部署輸出要能分辨「正常在跑」跟「卡住了」。每個主要 phase 完成後
# 印 elapsed，optional capability 被跳過時用 `⚠ skipped` 講清楚**為什麼**跳過，
# 不得跟真正的失敗混在一起看起來一樣。`PHASES` 陣列留到最後印 completion
# summary 用，不在這裡就印一次全部——那樣長時間 phase 進行中畫面仍然是啞的。
DEPLOY_START="$(date +%s)"
PHASES=()

fmt_duration() {  # fmt_duration <seconds>
  printf '%dm%02ds' "$(($1 / 60))" "$(($1 % 60))"
}

phase_start() {  # phase_start <label>
  PHASE_LABEL="$1"
  PHASE_T0="$(date +%s)"
  echo
  echo "▶▶ $PHASE_LABEL"
}

phase_ok() {  # phase_ok；用目前的 $PHASE_LABEL／$PHASE_T0
  local elapsed=$(( $(date +%s) - PHASE_T0 ))
  echo "✓ $PHASE_LABEL — $(fmt_duration "$elapsed")"
  PHASES+=("✓ $PHASE_LABEL — $(fmt_duration "$elapsed")")
}

phase_skip() {  # phase_skip <reason>
  local elapsed=$(( $(date +%s) - PHASE_T0 ))
  echo "⚠ $PHASE_LABEL skipped — $1"
  PHASES+=("⚠ $PHASE_LABEL — skipped（$1，$(fmt_duration "$elapsed")）")
}

# --- Readiness + completion summary（#144 D2／D3）-----------------------------
#
# 「腳本 exit 0」不等於「使用者點 URL 一定開得起來」——docker compose --wait
# 只驗健康檢查通過，不代表 nginx 那層的路由真的接得起來。這裡對外部入口各補一次
# 輕量 HTTP 往返，READY／DEGRADED／FAILED 三態明確分開，不讓「AI 沒拉到」看起來
# 跟「Product UI 打不開」一樣嚴重。

url_reachable() {  # url_reachable <url>；curl 打不到或非 2xx/3xx 都算不可達
  curl -fsS -o /dev/null -m 5 "$1" 2>/dev/null
}

# 候選的 LAN 位址——只是「猜看看哪張卡可能是外部連得到的」，不是真的驗證外部
# 可達性（那需要從別的網段實際打進來，部署腳本做不到）。寧可同時印
# localhost + 候選 IP 並附註「猜的」，也不要只印 localhost 讓 SSH 部署的人
# 誤以為那就是他該用的網址。
detect_lan_ip() {
  hostname -I 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i !~ /^127\./) { print $i; exit }}'
}

print_completion_summary() {  # print_completion_summary <mode>；mode = "Full Range" 或 "Stack Only"
  local mode="$1"
  local total=$(( $(date +%s) - DEPLOY_START ))
  local status="READY"
  local lan_ip
  lan_ip="$(detect_lan_ip || true)"

  echo
  echo "▶▶ Readiness"
  if ! command -v curl >/dev/null 2>&1; then
    # 沒有 curl 就不做往返檢查——工具缺席不等於服務掛了，不該讓 status 直接
    # 判 FAILED，但也不能假裝檢查過了印 ✓，所以就老實說沒查。
    echo "   ⚠ 沒有 curl，略過 Product UI／Grafana 的 HTTP 往返檢查"
  else
    if url_reachable "http://localhost:8090/healthz"; then
      echo "   ✓ Product UI (:8090) 可達"
    else
      echo "   ✗ Product UI (:8090) 打不到"
      status="FAILED"
    fi
    if url_reachable "http://localhost:3000/api/health"; then
      echo "   ✓ Grafana (:3000) 可達"
    else
      echo "   ✗ Grafana (:3000) 打不到"
      status="FAILED"
    fi
  fi
  # optional capability 被跳過只降級成 DEGRADED，不是 FAILED——核心可用時
  # 使用者仍然能開始一場演練，只是少了 AI 摘要或完整 Range 攻防面。
  if [ "$status" = "READY" ]; then
    for phase in "${PHASES[@]}"; do
      case "$phase" in ⚠*) status="DEGRADED" ;; esac
    done
  fi

  echo
  case "$status" in
    READY)    echo "✅ Cyber deployment ready" ;;
    DEGRADED) echo "⚠️  Cyber deployment DEGRADED —— 核心可用，但下面列的選配項目被跳過" ;;
    FAILED)   echo "❌ Cyber deployment FAILED —— 主要 UI 或 Grafana 打不到，見上方 Readiness" ;;
  esac
  echo
  echo "Mode: $mode"
  echo "Commit: $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "Elapsed: $(fmt_duration "$total")"
  echo
  echo "Phases:"
  for phase in "${PHASES[@]}"; do echo "  $phase"; done
  echo
  echo "Open Cyber UI:"
  echo "  Product UI       http://localhost:8090/"
  echo "  Battleboard      http://localhost:8090/battleboard/"
  echo "  Instructor Login http://localhost:8090/instructor-login/"
  echo "  Event Control    http://localhost:8090/event-control/"
  echo "  Purple Console   http://localhost:8090/purple/"
  if [ -n "$lan_ip" ] && [ "$lan_ip" != "localhost" ]; then
    echo
    echo "  這台機器偵測到的候選位址（SSH 部署到遠端主機時用這個，換成 localhost 打不到）："
    echo "  http://$lan_ip:8090/ ——猜的，不保證外部網段連得到，連不到請改 tunnel／VPN"
    echo "  ⚠️  走這個網址時 Instructor Login／Purple／Event Control 需要 session cookie，"
    echo "     compose 預設 ADMISSION_COOKIE_SECURE=0（配合這個 http 位址關掉 Secure）——"
    echo "     這是明文 cookie，只適合 demo／內網。要對外開放前先補 TLS，見 ui/README.md。"
  fi
  echo
  echo "Observability:"
  echo "  Grafana          http://localhost:3000/ (admin/admin)"
  # 用 if 而非 `[ ] && echo`：AI_ENABLED=0 時 `[ ]` 回非 0，裸接 `&&` 的那個統計
  # 值就是整條語句的退出碼——`set -e` 下會在這裡無聲中止腳本，不會印出後面任何
  # 東西也不會回報清楚的錯誤（本檔 §Preflight 的 NESTED 那行已經因為同一個
  # 陷阱加了 `|| true`，這裡直接用 if 從根源避開，不必每個站點都記得補）。
  if [ "$AI_ENABLED" = 1 ]; then
    echo "  Ollama           :11434（模型拉取失敗則功能自動停用，不影響其餘服務）"
  fi
  echo
  echo "Next:"
  echo "  1. 開啟 Product UI，用 Instructor Login 登入"
  echo "  2. 在 Instructor Console 選 scenario、按「預備」→ 開始演練"
  echo "  3. 跑一次煙霧測試：sudo bash test.sh"

  if [ "$status" = "FAILED" ]; then
    exit 1
  fi
  return 0
}

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

phase_start "Preflight（環境檢查）"

# 硬體規格對照 #137 實測拍板的 Minimum／Recommended（docs/deployment/hardware-baseline.md）。
# 純資訊，不阻斷部署——#137 的 headroom 數字是在對應規格下量出來的，規格不到不代表
# 部署不了，只是餘裕可能比實測數字更薄（Low 4C/8G 那輪本身就已經摸到過一次短暫
# CPU throttling）。三段都印，讓使用者自己判斷這台機器落在哪一段。
HW_MIN_CPU=4; HW_MIN_RAM=8
HW_REC_CPU=8; HW_REC_RAM=16
CPU_CORES="$(nproc 2>/dev/null || echo 0)"
RAM_GIB="$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo 2>/dev/null || echo 0)"
echo "   主機規格：${CPU_CORES} vCPU / ${RAM_GIB} GiB RAM"
if [ "$CPU_CORES" -lt "$HW_MIN_CPU" ] || [ "$RAM_GIB" -lt "$HW_MIN_RAM" ]; then
  echo "   ⚠ 低於 #137 Minimum（4 vCPU/8 GiB）—— Low(4C/8G) 實測都已出現短暫 CPU throttling，這台更少，餘裕可能更薄"
elif [ "$CPU_CORES" -lt "$HW_REC_CPU" ] || [ "$RAM_GIB" -lt "$HW_REC_RAM" ]; then
  echo "   ℹ 達 #137 Minimum（4 vCPU/8 GiB），未達 Recommended（8 vCPU/16 GiB）"
else
  echo "   ✓ 達 #137 Recommended（8 vCPU/16 GiB）"
fi

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
if PY_FOR_TESTS="$(purple_ensure_python "$REPO")"; then
  echo "   ✓ 測試用 python：$PY_FOR_TESTS"
else
  echo "⚠ 備不出帶 pytest 的 python（缺 python3-venv？）—— test.sh 的 T1/T2/T4 會略過"
  # 結尾一定要 `|| true`：`set -e` 下 `[ ] && { }` 條件不成立會讓整個 else 分支回非 0，
  # 進而讓整支腳本在這裡無聲中止。
  if [ "$INSTALL_DEPS" = 1 ]; then
    apt_install python3-venv
    purple_ensure_python "$REPO" >/dev/null || true
  fi
fi

if [ "$CAN_RANGE" = 0 ]; then
  echo "→ 只部署觀測棧（L1）。要完整 range：sudo bash deploy.sh --install-deps"
  echo "   細節見 scripts/range/HOST-SETUP.md"
  STACK_ONLY=1
fi

# AI 輔助（#131）：Ollama 是選配的 L1 附屬服務，跟 falco 一樣用 compose profile
# 開關（見下方 COMPOSE_ARGS）。硬性前提只有磁碟空間（模型檔 qwen2.5:3b 約 2GB，
# 加上 image 本身留 4GB 緩衝）——沒有就跳過整段並說明原因，不阻斷部署，比照
# L2 的 CAN_RANGE 分層邏輯。
AI_ENABLED=1
AI_MIN_FREE_KB=$((4 * 1024 * 1024))
AI_FREE_KB="$(df -Pk "$REPO" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -z "$AI_FREE_KB" ] || [ "$AI_FREE_KB" -lt "$AI_MIN_FREE_KB" ]; then
  echo "⚠ 磁碟空間不足 4GB（AI 模型檔約 2GB）—— AI 輔助段落本次跳過，不阻斷部署"
  AI_ENABLED=0
fi
phase_ok

if [ "$RESET" = 1 ]; then
  echo "▶ [reset] 拆除舊 range 與舊 compose"
  bash "$RANGE/teardown-range.sh" || true
  # 不帶 `--profile`：`down` 是全專案清除，不是「只拆目前這次要起的 profile」——
  # 帶著 `--profile falco` 原本就只是巧合地沒漏（ai／admission-e2e profile 起過
  # 的容器一樣要清掉，尤其現在 admission-e2e 已經是預設一律會起，見下方
  # #144 的說明）。留著單一 profile 旗標反而是「上次用什麼參數部署，這次
  # reset 才清得掉什麼」的隱性耦合。
  docker compose -f "$REPO/docker-compose.yml" down -v 2>/dev/null || true
fi

# --- L1 觀測／評估平面（compose）--------------------------------------------
phase_start "L1 平台（docker compose：觀測棧 ＋ Product UI）"
COMPOSE_ARGS=()
if [ "$FALCO_MODE" = "container" ]; then
  echo "   Falco 走容器（--profile falco）"
  COMPOSE_ARGS+=(--profile falco)
else
  echo "   Falco 不走容器（host kernel 不支援）；改由 range 的 golden 靶機 VM 承載"
fi
if [ "$AI_ENABLED" = 1 ]; then
  echo "   AI 輔助走容器（--profile ai）"
  COMPOSE_ARGS+=(--profile ai)
fi
# admission-e2e：Product UI（:8090）＋ Admission／Range Core／座位終端機。**一律帶上**——
# 不是選配。#144 之前 `deploy.sh` 從來沒帶這個 profile，於是「部署完成」的機器上根本沒有
# Product UI 可以連，任何 URL 導引都是連不上的死連結。這個 profile 本來就是為單機部署
# 設計的（見 docker-compose.yml 內建的固定 e2e token），不需要額外供應密鑰。
echo "   Product UI 走容器（--profile admission-e2e）"
COMPOSE_ARGS+=(--profile admission-e2e)
docker compose -f "$REPO/docker-compose.yml" "${COMPOSE_ARGS[@]}" \
  up -d --build --wait --wait-timeout 240
docker compose -f "$REPO/docker-compose.yml" "${COMPOSE_ARGS[@]}" ps
phase_ok

# AI 模型檔拉取——刻意放在 compose up「之後」、刻意 best-effort。healthcheck
# 只驗 server 有回應，不等模型拉完，就是為了不讓這步卡進 --wait-timeout；
# 這裡再失敗一次也只印警告，`generate()`（src/purple/ai/ollama_client.py）
# 呼叫時模型不存在會直接拿到錯誤回應，該函式已設計成回 None 而不是拋例外，
# 敘事生成／SOC Copilot 兩個下游功能因此照樣「沒有 AI 就沒有那段，其餘不受影響」。
phase_start "AI 模型（qwen2.5:3b，選配）"
if [ "$AI_ENABLED" = 1 ]; then
  echo "   拉取中（約 2GB），失敗不擋部署"
  if docker compose -f "$REPO/docker-compose.yml" --profile ai exec -T ollama \
      ollama pull qwen2.5:3b; then
    phase_ok
  else
    phase_skip "無網路／registry 擋掉——AI 輔助功能本次不可用，其餘部署不受影響"
  fi
else
  phase_skip "磁碟空間不足 4GB"
fi

phase_start "L2 Range（四區 VLAN + 靶機真 VM + 六台紅隊容器）"
if [ "$STACK_ONLY" = 1 ]; then
  phase_skip "只部署觀測平面（--stack-only 或環境缺 KVM/OVS/libvirt）"
  print_completion_summary "Stack Only"
  exit 0
fi

UP_ARGS=(--with-red)
if [ "$FALCO_MODE" = "vm" ]; then
  # golden 靶機（Falco+Alloy+app 已烤進）＋ 把真 Loki 掛上 VLAN10 讓 VM 推得到
  UP_ARGS+=(--with-falco)
fi
bash "$RANGE/range-up.sh" "${UP_ARGS[@]}"
phase_ok

# shellcheck source=scripts/range/zones.env
source "$RANGE/zones.env"
echo
echo "靶機 VM $TARGET_IP | 紅隊 $RED_IP_FIRST 起 $RED_COUNT 台 | MGMT $MGMT_STUB_IP / Loki $MGMT_LOKI_IP"
print_completion_summary "Full Range"
