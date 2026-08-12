# shellcheck shell=bash
# 共用：cloud image 下載 + 完整性把關。給 build-vm-*.sh source 用。
# 抽出來的原因：半截下載的 image 會讓 VM 開機讀到壞區塊 → EXT4 I/O error →
# kernel panic（症狀像網路問題其實是磁碟），這道把關要在每支建 VM 的腳本都成立。

# golden_stamp <repo>：golden image 的內容指紋 —— 所有烤入來源檔的 sha256。
#
# 為什麼需要：golden 是一顆 qcow2，光看檔案在不在無法判斷它是用哪一版來源烤的。
# 改了靶機 app 或 Alloy 設定卻沿用舊 golden，會得到「檔案存在但功能不對」的假象，
# 而且症狀出現在很後面（測試打不到 :80、Loki 沒事件），極難回推。
# 產出時蓋 stamp、使用前比對，指紋不合就自動重烤。
golden_stamp() {
  local repo="$1"
  cat "$repo/deploy/range-target/bake.sh" \
      "$repo/deploy/range-target/app.py" \
      "$repo/deploy/range-target/config.alloy" \
      "$repo/deploy/falco/rules.d/purplescope.yaml" \
      "$repo/scripts/range/zones.env" \
      "$repo/src/purple/__init__.py" \
      "$repo/src/purple/harness/__init__.py" \
      "$repo/src/purple/harness/attacker.py" \
      "$repo/src/purple/harness/loki_probe.py" \
      "$repo/src/purple/harness/schema.py" \
      "$repo/src/purple/harness/waiting.py" \
      "$repo/src/purple/response/__init__.py" \
      "$repo/src/purple/response/agent.py" \
      "$repo/src/purple/response/queue.py" \
      "$repo/src/purple/response/direct_block.py" \
      "$repo/src/purple/response/http_link.py" \
      "$repo/src/purple/response/service.py" 2>/dev/null \
    | sha256sum | cut -d' ' -f1
}

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
