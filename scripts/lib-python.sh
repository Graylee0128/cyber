#!/usr/bin/env bash
# 選一個「真的 import 得到 pytest」的 python 直譯器。
#
# 為什麼需要這支：deploy.sh / test.sh 都必須用 sudo 跑（操作 netns、libvirt、OVS、
# docker 都要 root），而 sudo 會切到 **root 的 PATH 與 site-packages**。使用者用
# venv 或 `pip install --user` 裝的 pytest，在 root 下完全看不到。
# 2026-08-09 實測：大主機上 `sudo bash test.sh` 的 T1/T2/T4 三層全部倒在
# `/usr/bin/python3: No module named pytest`，卻與程式碼本身無關。
#
# 對策：以 **repo 內的 .venv 為準**，root 與非 root 看到的是同一套依賴，行為一致。
# 找不到就地建一個（`.venv` 已在 .gitignore；sudo 下建完 chown 回原使用者，
# 否則之後非 root 跑 pytest 會寫不進 __pycache__）。

purple_has_pytest() {  # purple_has_pytest <python 路徑>
  [ -n "${1:-}" ] && [ -x "$1" ] && "$1" -c 'import pytest' >/dev/null 2>&1
}

# purple_pick_python <repo> [allow_create=1]
# 成功時把可用的 python 路徑印到 **stdout**（其餘訊息一律走 stderr，呼叫端用
# $(...) 取值才不會被說明文字污染）。找不到且不允許建 venv 時回非 0。
purple_pick_python() {
  local repo="$1" allow_create="${2:-1}" venv c
  venv="$repo/.venv/bin/python"

  for c in "${PURPLE_PY:-}" "$venv" "$(command -v python3 || true)" "$(command -v python || true)"; do
    if purple_has_pytest "$c"; then echo "$c"; return 0; fi
  done

  [ "$allow_create" = 1 ] || return 1

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

  # sudo 下建的 venv 屬 root，交還原使用者，之後非 root 也能用。
  if [ -n "${SUDO_USER:-}" ] && command -v chown >/dev/null 2>&1; then
    chown -R "$SUDO_USER" "$repo/.venv" 2>/dev/null || true
  fi

  if purple_has_pytest "$venv"; then echo "$venv"; return 0; fi
  return 1
}
