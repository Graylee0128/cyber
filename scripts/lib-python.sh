#!/usr/bin/env bash
# 找／備一個「真的 import 得到 pytest」的 python 直譯器。
#
# 為什麼需要這支：deploy.sh / test.sh 都必須用 sudo 跑（操作 netns、libvirt、OVS、
# docker 都要 root），而 sudo 會切到 **root 的 PATH 與 site-packages**。使用者用
# venv 或 `pip install --user` 裝的 pytest，在 root 下完全看不到。
# 2026-08-09 實測：大主機上 `sudo bash test.sh` 的 T1/T2/T4 三層全部倒在
# `/usr/bin/python3: No module named pytest`，卻與程式碼本身無關。
#
# 對策：以 **repo 內的 .venv 為準**，root 與非 root 看到的是同一套依賴，行為一致。
#
# 兩個函式刻意分開（2026-08-09 code review）：一個叫 find 卻會裝套件的函式，
# 呼叫端無從得知它會不會動到系統。現在 find＝只找不動，ensure＝找不到就建，
# 副作用寫在名字上。

purple_has_pytest() {  # purple_has_pytest <python 路徑>
  [ -n "${1:-}" ] && [ -x "$1" ] && "$1" -c 'import pytest' >/dev/null 2>&1
}

# purple_find_python <repo>
# **純查找，不產生任何副作用。** 找到就把路徑印到 stdout 並回 0；找不到回 1。
# 順序：PURPLE_PY 覆寫 → repo 內 .venv → PATH 上的 python3 / python。
purple_find_python() {
  local repo="$1" c
  for c in "${PURPLE_PY:-}" "$repo/.venv/bin/python" \
           "$(command -v python3 || true)" "$(command -v python || true)"; do
    if purple_has_pytest "$c"; then echo "$c"; return 0; fi
  done
  return 1
}

# purple_ensure_python <repo>
# 先 find；找不到才**建 repo 內的 .venv 並安裝依賴**（會動到檔案系統）。
# 成功時把路徑印到 **stdout**；其餘訊息一律走 stderr，呼叫端用 $(...) 取值才不會被污染。
purple_ensure_python() {
  local repo="$1" venv="$1/.venv/bin/python"

  if purple_find_python "$repo"; then return 0; fi

  echo "▶ 找不到帶 pytest 的 python，就地建 venv：$repo/.venv" >&2
  if ! python3 -m venv "$repo/.venv" >&2; then
    echo "❌ python3 -m venv 失敗。先裝：sudo apt-get install -y python3-venv" >&2
    return 1
  fi
  # 分兩步裝：專案本體與 pytest 各自失敗時訊息才分得清楚
  # （`-e "$repo[dev]"` 這種 extras 寫法在不同 pip 版本行為不一致，不用）。
  "$venv" -m pip install -q --upgrade pip >&2 || true
  "$venv" -m pip install -q -e "$repo" >&2 || { echo "❌ pip install -e 專案失敗" >&2; return 1; }
  "$venv" -m pip install -q "pytest>=8.0" >&2 || { echo "❌ pip install pytest 失敗" >&2; return 1; }

  # sudo 下建的 venv 屬 root，交還原使用者，之後非 root 也能用（否則寫不進 __pycache__）。
  if [ -n "${SUDO_USER:-}" ] && command -v chown >/dev/null 2>&1; then
    chown -R "$SUDO_USER" "$repo/.venv" 2>/dev/null || true
  fi

  if purple_has_pytest "$venv"; then echo "$venv"; return 0; fi
  return 1
}
