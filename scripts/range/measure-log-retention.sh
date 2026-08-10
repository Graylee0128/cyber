#!/usr/bin/env bash
# 票 #12 / Slice 3-② —— raw log 保留時段 + 磁碟量測（真環境數字，供 Exercise Report）。
#
# 純函式 retention/window.py 管「開/關窗」邏輯（已單元測試）；loki-config 的 compactor
# retention_enabled + retention_period 管 Loki 真的刪除。這支腳本量的是**環境數字**：
#   - Loki 目前存了多少行（各 job）
#   - Loki 資料 volume 佔多少磁碟
#   - 生效的 retention_period
# 不是 pass/fail 測試，是量測報告。前提：compose 全棧已起（docker compose up）。
set -euo pipefail

LOKI_URL="${PURPLE_LOKI_URL:-http://localhost:3100}"
SINCE_S="${SINCE_S:-3600}"   # 統計最近多久的行數，預設 1h

echo "▶ Loki 保留量測（$LOKI_URL）"

count_job() {  # count_job <job-label-selector>
  local sel="$1"
  local q; q=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "sum(count_over_time(${sel}[${SINCE_S}s]))")
  # 用 instant query 拿總數；失敗回 0（不讓量測腳本本身炸掉整個報告）
  curl -fsS "$LOKI_URL/loki/api/v1/query?query=$q" 2>/dev/null \
    | python3 -c "import json,sys;
d=json.load(sys.stdin);
r=(d.get('data') or {}).get('result') or [];
print(int(float(r[0]['value'][1])) if r else 0)" 2>/dev/null || echo 0
}

# 選擇器用單引號包住、內含裸雙引號（LogQL 要的是 {app="..."}，不能有反斜線）。
APP_LINES="$(count_job '{app="vulnerable-app"}')"
FALCO_LINES="$(count_job '{job="falco"}')"
echo "   • app 行數（近 ${SINCE_S}s）：$APP_LINES"
echo "   • falco 行數（近 ${SINCE_S}s）：$FALCO_LINES"

echo "▶ Loki 資料 volume 磁碟用量"
VOL="$(docker volume ls -q | grep -E 'lokidata$' | head -1 || true)"
if [ -n "$VOL" ]; then
  # loki 是 distroless、無 shell，改用 throwaway alpine 掛同一個 volume 量 du。
  SIZE="$(docker run --rm -v "$VOL":/loki alpine du -sh /loki 2>/dev/null | awk '{print $1}')"
  echo "   • volume $VOL：$SIZE"
else
  echo "   • 找不到 lokidata volume（compose 起了嗎？）"
fi

echo "▶ 生效的 retention 設定（loki-config）"
grep -E 'retention_enabled|retention_period' "$(dirname "$0")/../../deploy/loki/loki-config.yaml" | sed 's/^/   /'

echo "✅ 量測完成。把上面數字填進 Exercise Report 的儲存成本欄。"
