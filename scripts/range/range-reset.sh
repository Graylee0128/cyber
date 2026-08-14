#!/usr/bin/env bash
# 票 #13 Slice 4 —— Reset：把整組 range 拆乾淨再重起。演練之間一鍵回到已知狀態。
#
# 選項原封轉給 range-up.sh（--with-red / --no-falco）。
# golden image 不刪（重建很久）；要連 golden 一起丟：加 --purge-golden。
# 需 root。**不在 CI**。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="/var/lib/libvirt/images"
GOLDEN="$CACHE/range-target-golden.qcow2"

PURGE_GOLDEN=0
UP_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --purge-golden) PURGE_GOLDEN=1 ;;
    *) UP_ARGS+=("$arg") ;;
  esac
done

echo "▶▶ Reset：拆除舊 range"
bash "$DIR/teardown-range.sh" || true

if [ "$PURGE_GOLDEN" = 1 ]; then
  echo "▶ 刪除 golden image（$GOLDEN）"
  rm -f "$GOLDEN"
fi

echo "▶▶ Reset：重起 range"
bash "$DIR/range-up.sh" "${UP_ARGS[@]}"

echo "▶▶ Reset 完成。"
