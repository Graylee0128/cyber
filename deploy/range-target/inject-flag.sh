#!/bin/bash
# 開機時把當場的 flag 注入 vault.flag（票 #45）—— 由 systemd oneshot 在 mariadb 起來後跑。
#
# flag 的值**不烤進 golden**：range-up 產生後由 cloud-init 寫到 $FLAG_FILE，本腳本開機
# 讀它並 UPDATE。golden 裡永遠是 seed.sql 的 placeholder，改 flag 不觸發重烤（見 #45）。
#
# 冪等：同一顆 VM 重開機讀到同一個 $FLAG_FILE，UPDATE 成同值，無副作用。
# 找不到 flag 檔就保留 placeholder 並記一行 —— 不讓演練「靜默地用假 flag 開跑」。
set -Eeuo pipefail

FLAG_FILE="${TARGET_FLAG_FILE:-/etc/purplescope/flag.txt}"

if [ ! -s "$FLAG_FILE" ]; then
  echo "inject-flag: 找不到 $FLAG_FILE —— vault.flag 維持 placeholder（range-up 有產生 flag 嗎？）" >&2
  exit 0
fi

FLAG="$(head -n1 "$FLAG_FILE" | tr -d '\r\n')"

# 用本機 socket 以 root 進 mysql（無需密碼），UPDATE 唯一那列。
# 以 stdin 餵 SQL 並把 flag 當變數綁定，避免值裡的字元破壞語句。
mysql --protocol=socket -u root <<SQL
SET @flag = $(printf '%s' "$FLAG" | sed "s/'/''/g; s/^/'/; s/$/'/");
UPDATE vault.flag SET token = @flag WHERE id = 1;
SQL

echo "inject-flag: vault.flag 已更新為當場 flag（${FLAG:0:6}…）"
