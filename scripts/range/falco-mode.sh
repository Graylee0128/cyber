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

# 三態，不是二態（2026-08-09 code review）：原本寫 `major >= 7 → vm`，等於宣稱
# 「kernel 8、9、10 也不相容」，但我們只實測過 7.0.0-28。把「實測失敗」與「沒測過」
# 混為一談，會讓日後 Falco 出了支援新 kernel 的驅動時，這裡繼續無聲地強制走 VM。
#
#   已知不相容（實測）→ vm，安靜地走
#   已知相容（實測）  → container
#   沒測過            → vm（VM 自帶 kernel 6.8，對 host kernel 免疫，是安全的預設）
#                       但**印一行到 stderr**說明這是保守預設、值得重測
#
# 覆寫：FALCO_MODE=container|vm。新增實測結果請改這兩個清單，別動邏輯。
KNOWN_INCOMPATIBLE_MAJORS="${FALCO_KNOWN_INCOMPATIBLE_MAJORS:-7}"  # 實測 scap_init 失敗
KNOWN_COMPATIBLE_MAJORS="${FALCO_KNOWN_COMPATIBLE_MAJORS:-5 6}"    # 實測可跑容器 Falco

if [ -n "${FALCO_MODE:-}" ]; then
  echo "$FALCO_MODE"
  exit 0
fi

major="$(uname -r | cut -d. -f1)"

in_list() {  # in_list <needle> <space-separated list>
  local needle="$1" item
  for item in $2; do [ "$item" = "$needle" ] && return 0; done
  return 1
}

if in_list "$major" "$KNOWN_INCOMPATIBLE_MAJORS"; then
  echo "vm"
elif in_list "$major" "$KNOWN_COMPATIBLE_MAJORS"; then
  echo "container"
else
  echo "· kernel $major.x 未實測過容器 Falco，保守走 vm 模式（VM 自帶 kernel，對 host 免疫）。" >&2
  echo "  想試容器模式：FALCO_MODE=container；確認可行後把 $major 加進 FALCO_KNOWN_COMPATIBLE_MAJORS。" >&2
  echo "vm"
fi
