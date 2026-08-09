#!/usr/bin/env bash
# PurpleScope —— 一鍵測試。這是**唯一**你需要記得的測試指令，其餘驗證器由它呼叫。
#
#   sudo bash test.sh            # 依現有環境跑到最深（單元 → 整合 → range 契約 → 真環境全鏈）
#   bash test.sh --unit-only     # 只跑不需 range 的部分（單元 + compose 整合）
#
# 分層，每層都會說「跑了／略過，為什麼」——**不會把略過講成通過**：
#   T1 單元／契約   純函式與管路契約（需 Postgres，compose 已起即可）
#   T2 compose 整合 真 SQLi / OTLP metric / Evidence API / lifecycle 走完管線
#   T3 range 契約   四區方向性防火牆 + 六台紅隊 source IP 可分辨（需 range）
#   T4 真環境全鏈   紅隊隔真 VLAN 打靶機 VM → Falco → Alloy → Loki → Core Event（需 golden range）
#
# Falco 兩案（見 scripts/range/falco-mode.sh）：container 走 T2 的 falco 測試；
# vm 走 T4 的真環境全鏈。覆寫：FALCO_MODE=container|vm。
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RANGE="$REPO/scripts/range"
UNIT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --unit-only) UNIT_ONLY=1 ;;
    -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知選項：$arg（--unit-only）"; exit 2 ;;
  esac
done

PY="$(command -v python3 || command -v python)"
[ -n "$PY" ] || { echo "❌ 找不到 python3"; exit 1; }

FALCO_MODE="$(bash "$RANGE/falco-mode.sh")"
export PURPLE_AUTO_COMPOSE=0
export PURPLE_PG_DSN="${PURPLE_PG_DSN:-postgresql://purple:purple@localhost:5432/purple}"
export PURPLE_APP_URL="${PURPLE_APP_URL:-http://localhost:8080}"
export PURPLE_ENGINE_URL="${PURPLE_ENGINE_URL:-http://localhost:8001}"
export PURPLE_LOKI_URL="${PURPLE_LOKI_URL:-http://localhost:3100}"

fails=0
declare -a SUMMARY

record() { SUMMARY+=("$1"); }

echo "════════════════════════════════════════════════════════════"
echo " PurpleScope 測試   Falco 模式：$FALCO_MODE   (kernel $(uname -r))"
echo "════════════════════════════════════════════════════════════"

# --- T1 單元／契約 -----------------------------------------------------------
echo
echo "▶▶ T1 單元／契約測試（純函式 + 管路契約；需 Postgres）"
if "$PY" -m pytest -q -m "not integration"; then
  record "T1 單元／契約：✅"
else
  record "T1 單元／契約：❌"; fails=1
fi

# --- T2 compose 整合 ---------------------------------------------------------
echo
echo "▶▶ T2 compose 整合（真 SQLi / OTLP metric / Evidence API / lifecycle）"
if docker compose -f "$REPO/docker-compose.yml" ps --status running -q 2>/dev/null | grep -q .; then
  if [ "$FALCO_MODE" = "container" ]; then
    export PURPLE_FALCO_ENABLED=1
    echo "   （含容器 Falco 管線測試）"
  else
    echo "   （容器 Falco 測試略過：host kernel 不支援，改由 T4 的 VM 內 Falco 驗）"
  fi
  if "$PY" -m pytest -q -s -m integration \
      --ignore="$REPO/tests/integration/test_falco_range_chain.py"; then
    record "T2 compose 整合：✅"
  else
    record "T2 compose 整合：❌"; fails=1
  fi
else
  record "T2 compose 整合：⏭ 略過（compose 沒在跑 —— 先 sudo bash deploy.sh）"
fi

if [ "$UNIT_ONLY" = 1 ]; then
  echo; echo "──────── 摘要（--unit-only）────────"
  printf '%s\n' "${SUMMARY[@]}"
  exit "$fails"
fi

# --- T3 range 契約 -----------------------------------------------------------
echo
echo "▶▶ T3 range 契約（四區方向性防火牆 + 六台紅隊 source IP 可分辨）"
if ip netns list 2>/dev/null | grep -q ns-mgmt; then
  if [ "$(id -u)" != 0 ]; then
    record "T3 range 契約：⏭ 略過（需 root —— 用 sudo bash test.sh）"
  elif bash "$RANGE/verify-range.sh"; then
    record "T3 range 契約：✅"
  else
    record "T3 range 契約：❌"; fails=1
  fi
else
  record "T3 range 契約：⏭ 略過（range 沒建 —— 先 sudo bash deploy.sh）"
fi

# --- T4 真環境全鏈 -----------------------------------------------------------
echo
echo "▶▶ T4 真環境全鏈（紅隊 → 靶機 VM → Falco → Alloy → Loki → Core Event）"
if [ "$FALCO_MODE" != "vm" ]; then
  record "T4 真環境全鏈：⏭ 略過（Falco 走容器模式，該鏈由 T2 涵蓋）"
elif ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^range-red1$'; then
  record "T4 真環境全鏈：⏭ 略過（無紅隊容器 —— 先 sudo bash deploy.sh）"
elif ! virsh domstate range-target 2>/dev/null | grep -q running; then
  record "T4 真環境全鏈：⏭ 略過（靶機 VM 沒在跑）"
else
  export PURPLE_RANGE_CHAIN=1
  if "$PY" -m pytest -q -s -m integration "$REPO/tests/integration/test_falco_range_chain.py"; then
    record "T4 真環境全鏈：✅"
  else
    record "T4 真環境全鏈：❌"; fails=1
  fi
fi

# --- 摘要 --------------------------------------------------------------------
echo
echo "════════════════════ 測試摘要 ════════════════════"
printf '%s\n' "${SUMMARY[@]}"
echo "═════════════════════════════════════════════════"
if [ "$fails" -ne 0 ]; then
  echo "❌ 有測試未通過（見上）"
  exit 1
fi
echo "✅ 全數通過（略過項已於摘要標明原因）"
