# shellcheck shell=bash
# 共用：cloud image 下載 + 完整性把關。給 build-vm-*.sh source 用。
# 抽出來的原因：半截下載的 image 會讓 VM 開機讀到壞區塊 → EXT4 I/O error →
# kernel panic（症狀像網路問題其實是磁碟），這道把關要在每支建 VM 的腳本都成立。

# img_ok <file>：qemu-img check 通過才算完整（0=好、3=只是 leak 也可用）。
img_ok() { local rc; qemu-img check "$1" >/dev/null 2>&1; rc=$?; [ "$rc" = 0 ] || [ "$rc" = 3 ]; }

# fetch_cloudimg <url> <dest>：壞快取自動刪、續傳到 .part、驗過完整才 mv 成正式檔。
# 半截檔永遠不會被當成 base。斷線可重跑續傳。
fetch_cloudimg() {
  local url="$1" dest="$2"
  if [ -f "$dest" ] && ! img_ok "$dest"; then
    echo "   ⚠ 快取 image 損毀（多半上次下載中斷），刪除重抓"
    rm -f "$dest"
  fi
  if [ ! -f "$dest" ]; then
    echo "   下載中（~600MB，第一次較久；斷線可重跑續傳）..."
    curl -fL -C - "$url" -o "$dest.part"
    img_ok "$dest.part" || { echo "❌ 下載的 image 仍損毀，請重跑續傳"; return 1; }
    mv "$dest.part" "$dest"
  fi
}
