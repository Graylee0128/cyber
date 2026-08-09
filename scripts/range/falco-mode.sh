#!/usr/bin/env bash
# Falco 部署模式選擇器（票 #9）。印出 "container" 或 "vm"，deploy.sh / test.sh 共用。
#
# 兩案並存，不是二選一的取捨，而是**依 host kernel 自動選**：
#
#   (1) container —— 舊方案：Falco 跑在 compose 容器裡，直接吃 host kernel。
#       快、單機、和其餘觀測棧同一個 compose。**要 host kernel 是 Falco 驅動支援的版本。**
#
#   (2) vm —— 新方案：Falco 跑在 golden 靶機 VM(kernel 6.8) 內，事件經 Alloy 推回
#       Z-MGMT 的 Loki。繞開 host kernel 限制，而且更貼近產品形態（Falco 真的在
#       Z-TARGET 側的靶機上）。代價是要巢狀虛擬化。
#
# 為什麼要自動判斷（2026-08-09 大主機實測）：host kernel 7.0.0-28 上，Falco 0.39.2 與
# 0.44.1 的 modern-eBPF 都因 CO-RE relocation 對不上而 scap_init 失敗
# （`struct mm_struct.rss_stat` 版面在新 kernel 變了），kmod 驅動同樣不支援該 kernel。
# 這是 Falco 驅動與前沿 kernel 的落差，不是設定問題 —— 與其讓部署腳本撞牆，不如先問
# kernel 再決定走哪案。
#
# 覆寫：FALCO_MODE=container|vm 直接指定（例如你想在相容 kernel 上強制走 VM 驗收）。
set -euo pipefail

# 已知不支援容器 Falco 的 host kernel 主版本下限（含）。7.x 起實測失敗。
INCOMPATIBLE_MAJOR="${FALCO_INCOMPATIBLE_MAJOR:-7}"

if [ -n "${FALCO_MODE:-}" ]; then
  echo "$FALCO_MODE"
  exit 0
fi

major="$(uname -r | cut -d. -f1)"
if [ "$major" -ge "$INCOMPATIBLE_MAJOR" ] 2>/dev/null; then
  echo "vm"
else
  echo "container"
fi
