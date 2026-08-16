#!/usr/bin/env bash
# 補 #62 留下的接線缺口：seat_provisioner.py 是「host 側常駐服務」（見該檔檔頭
# docstring），刻意設計成不能用 docker-compose 起——它要直接動 host 的
# ovs-vsctl／ip netns／nft／iptables，塞進容器等於違反 WS5 spec §2.3 已經
# 拒絕過的「讓 Z-APP 服務拿到 host OVS 權限」。所以正確的起法是像
# attach-mgmt.sh／stub_listener.py 那樣，由 range-up.sh／teardown-range.sh
# 在 host 側背景啟停，不是 docker compose service。
#
# 用法：
#   sudo bash seat-provisioner-daemon.sh start   # 冪等；已在跑就跳過
#   sudo bash seat-provisioner-daemon.sh stop
#   sudo bash seat-provisioner-daemon.sh status
#
# 覆寫點（環境變數，皆有預設值，方便 admission-e2e / 開發環境直接可用）：
#   ADMISSION_URL                預設 http://localhost:8002（compose 的 admission port）
#   ADMISSION_PROVISIONER_TOKEN  預設沿用 compose 寫死的 e2e-service-token（dev 用途；
#                                正式環境應覆寫成非 e2e 的 instructor token）
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="/tmp/range-seat-provisioner.pid"
LOGFILE="/tmp/range-seat-provisioner.log"
ADMISSION_URL="${ADMISSION_URL:-http://localhost:8002}"

is_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "${1:-}" in
  start)
    if is_running; then
      echo "   · seat provisioner 已在跑（pid $(cat "$PIDFILE")），略過"
      exit 0
    fi
    # best-effort：admission 沒起（例如純 --stack-only 或沒開 admission-e2e
    # profile）就跳過，不擋 range-up——跟 attach-mgmt.sh 同一種容錯風格。
    if ! curl -s -m3 -o /dev/null "$ADMISSION_URL/admission/seats/pending?team=red" \
         -H "Authorization: Bearer x" 2>/dev/null; then
      echo "   ⚠ admission（$ADMISSION_URL）連不到，seat provisioner 本次跳過"
      echo "     admission-e2e profile 起了嗎？docker compose --profile admission-e2e up -d"
      exit 0
    fi
    export ADMISSION_URL
    export ADMISSION_PROVISIONER_TOKEN="${ADMISSION_PROVISIONER_TOKEN:-e2e-service-token}"
    nohup python3 "$DIR/seat_provisioner.py" >>"$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    echo "   ✓ seat provisioner 啟動（pid $!，log $LOGFILE）"
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
    fi
    pkill -f "scripts/range/seat_provisioner.py" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "   · seat provisioner 已停"
    ;;
  status)
    if is_running; then
      echo "running (pid $(cat "$PIDFILE"))"
    else
      echo "stopped"
    fi
    ;;
  *)
    echo "用法：$0 start|stop|status" >&2
    exit 2
    ;;
esac
