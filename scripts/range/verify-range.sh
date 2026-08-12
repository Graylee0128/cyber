#!/usr/bin/env bash
# 票 #13 —— 從各區實測跨世代契約 + 六台 red source IP 可分辨。
#
# 兩種佈局都認得（自動偵測，同一支驗證器）：
#   netns 模式（Slice 1 / CI）：靶機與紅隊都是 network namespace
#   VM 模式  （Slice 4 / 大主機）：靶機是真 VM(10.167.20.10)，紅隊是真容器
#
# 紅隊容器一樣用 `ip netns exec range-redN`（attach-red 建了 /var/run/netns 連結），
# 所以跑的是 **host 的 python3**，容器映像裡有沒有 python 都無所謂。
#
# 每條契約從**對應的區**跑（契約 2 若在 target 跑會連到自己而假通）。任一條破 → exit 1。需 root。
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
VT="$REPO/scripts/verify_topology.py"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"
MGMT="$MGMT_STUB_IP"
RECEIVER="$MGMT_RECEIVER_IP"
TARGET="$TARGET_IP"
APP="$APP_STUB_IP"
EDGE="$EDGE_STUB_IP"
BLUE="$BLUE_STUB_IP"
ENGINE="$MGMT_ENGINE_IP"
RED_HOST_FIRST="${RED_IP_FIRST##*.}"
CONSOLE="/tmp/range-target-console.log"
NONCE_FILE="/tmp/range-target-boot.nonce"
LOKI_URL="${PURPLE_LOKI_URL:-http://localhost:3100}"
fails=0

has_ns() { ip netns list 2>/dev/null | awk '{print $1}' | grep -qx "$1"; }

# 從某台紅隊節點對靶機 :80 送一個**真的 HTTP request**（RED→TARGET:80 是 §12.3 允許的）。
# 一定要送 request 不能只 connect：VM 模式的證據來源是靶機 app 的 log，而 app 只在
# 處理完請求時才寫 source_ip。用 /probe（未知路徑走 404 分支會寫 log）而非 /healthz
# （刻意不寫 log）或 /exec、/readsecret（會觸發 Falco 規則、污染 T4 的事件計數）。
PROBE_PY="$(mktemp)"
cat > "$PROBE_PY" <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(3)
s.connect((sys.argv[1], 80))   # 連得上是唯一的硬條件
# 送不出去不算失敗：netns 模式的 stub listener accept 完就 conn.close()，
# 這個 sendall 可能撞上 RST；但 source IP 在 accept 當下就記下來了。
try:
    s.sendall(b"GET /probe HTTP/1.1\r\nHost: range-target\r\nConnection: close\r\n\r\n")
    s.recv(256)
except OSError:
    pass
s.close()
PY
trap 'rm -f "$PROBE_PY"' EXIT

probe_target_from_red() {  # probe_target_from_red <紅隊節點名>
  ip netns exec "$1" python3 "$PROBE_PY" "$TARGET" 2>/dev/null
}

# 查 Loki：某個 selector 在 <since_s> 秒內有幾行。用於「現在這一刻通不通」的實證。
loki_recent_lines() {  # loki_recent_lines <selector> <since_s>
  python3 - "$LOKI_URL" "$1" "$2" <<'PY'
import json, sys, time, urllib.parse, urllib.request
base, selector, since = sys.argv[1].rstrip("/"), sys.argv[2], float(sys.argv[3])
end = time.time(); start = end - since
q = urllib.parse.urlencode({"query": selector, "start": str(int(start * 1e9)),
                            "end": str(int(end * 1e9)), "limit": "1000",
                            "direction": "backward"})
try:
    with urllib.request.urlopen(f"{base}/loki/api/v1/query_range?{q}", timeout=5) as r:
        payload = json.load(r)
except Exception:
    print(-1); sys.exit(0)          # -1＝查不動（與「查得動但 0 行」要分得開）
streams = (payload.get("data") or {}).get("result") or []
print(sum(len(s.get("values", [])) for s in streams))
PY
}

# --- 偵測佈局 ---------------------------------------------------------------
if has_ns ns-target; then TARGET_MODE="netns"; else TARGET_MODE="vm"; fi
if has_ns ns-red1; then RED_NS_PREFIX="ns-red"; RED_MODE="netns"
elif has_ns range-red1; then RED_NS_PREFIX="range-red"; RED_MODE="container"
else RED_NS_PREFIX=""; RED_MODE="none"; fi
echo "=== 佈局：靶機=$TARGET_MODE｜紅隊=$RED_MODE ==="

# --- 契約 1：TARGET → MGMT --------------------------------------------------
echo "=== 契約 1：telemetry 三埠 + 指定 receiver:8000；其他 MGMT:8000/:22 不通 ==="
if [ "$TARGET_MODE" = "netns" ]; then
  ip netns exec ns-target python3 "$VT" --from-zone target \
    --mgmt "$MGMT" --receiver "$RECEIVER" || fails=1
else
  # VM 模式分兩段，而且**只有現場實證那段決定通過與否**。
  #
  # 為什麼要改（2026-08-09 code review）：原本整條契約 1 靠 grep console log 的
  # SLICE2A-RESULT。那個檔只在建新 VM 時被 rm，所以 VM 掛掉、防火牆被改、Loki 從
  # VLAN10 拆掉之後，它照樣回報通過 —— 四條契約裡唯一無法對開機後的退化變紅的一條。
  #
  #   a) :3100 —— 現場實證：打靶機一下，看 Loki 是否在**剛剛**收到它推來的新行。
  #      這同時證明 TARGET→MGMT:3100 此刻通（不是開機當時通）。
  #   b) :9090 / :4317 —— 靶機沒有東西會往這兩個 port 推，host 也刻意不持有 VM 憑證，
  #      只能沿用開機自驗。但改成必須：VM 現在 running ＋ console log 的 boot nonce
  #      與 host 端 sidecar 相符（證明那份記錄屬於**這顆**VM，不是上一輪殘骸）。

  if ! virsh domstate range-target 2>/dev/null | grep -q running; then
    echo "  ✗ 靶機 VM 不在 running —— 契約 1 無從談起"; fails=1
  else
    # (a) 現場實證 :3100
    if [ "$RED_MODE" = "none" ]; then
      echo "  ⚠ 無紅隊節點，無法主動觸發靶機產生新遙測；改看它既有的推送"
    else
      probe_target_from_red "${RED_NS_PREFIX}1" || true
    fi
    fresh=0
    for _ in $(seq 1 10); do   # Alloy 推送有批次延遲，給 ~20s
      fresh="$(loki_recent_lines '{app="range-target"}' 60)"
      [ "$fresh" != "-1" ] && [ "$fresh" -gt 0 ] && break
      sleep 2
    done
    if [ "$fresh" = "-1" ]; then
      echo "  ✗ Loki 查不動（$LOKI_URL）—— 無法對契約 1 做現場實證"; fails=1
    elif [ "$fresh" -gt 0 ]; then
      echo "  ✓ :3100 現場實證：Loki 近 60s 收到靶機推來的 $fresh 行（TARGET→MGMT 此刻通）"
    else
      echo "  ✗ :3100 現場實證失敗：打了靶機但 Loki 60s 內沒收到任何新行"
      echo "    → 靶機 VM 的 Alloy 沒推上來，或 Loki 沒掛在 VLAN$Z_MGMT_VLAN（attach-mgmt.sh）"
      fails=1
    fi

    # (b) :9090/:4317、receiver:8000 正向與其他 MGMT:8000/:22 反向的開機自驗，
    #     加上「這份證據屬於現在這顆 VM」的檢查。
    want_nonce="$(cat "$NONCE_FILE" 2>/dev/null || echo "")"
    if [ -z "$want_nonce" ]; then
      echo "  ✗ 找不到 boot nonce（$NONCE_FILE）—— 無法確認開機自驗屬於這顆 VM"; fails=1
    elif ! grep -q "nonce=$want_nonce" "$CONSOLE" 2>/dev/null; then
      echo "  ✗ console log 的 boot nonce 對不上 —— 那份自驗是舊 VM 留下的，不採信"; fails=1
    elif ! grep -q "非 telemetry :22 不通" "$CONSOLE" 2>/dev/null; then
      echo "  ✗ console 沒有 :22 反向斷言 —— 舊版探測腳本的 PASS 不採信，請重建 VM"; fails=1
    elif ! grep -q "response receiver $RECEIVER:8000 通" "$CONSOLE" 2>/dev/null; then
      echo "  ✗ console 沒有指定 receiver:8000 正向證據 —— 舊版探測腳本不採信"; fails=1
    elif ! grep -q "非 receiver MGMT $MGMT:8000 不通" "$CONSOLE" 2>/dev/null; then
      echo "  ✗ console 沒有其他 MGMT:8000 反向證據 —— 最小權限未獲證明"; fails=1
    elif ! grep -q "Z-APP OK: TARGET(VM) -> APP $APP:443 通" "$CONSOLE" 2>/dev/null; then
      echo "  ✗ console 沒有 TARGET→APP:443 正向證據"; fails=1
    elif ! grep -q "Z-APP OK: TARGET(VM) -> APP $APP:22 不通" "$CONSOLE" 2>/dev/null; then
      echo "  ✗ console 沒有 TARGET→APP 非 HTTPS 負向證據"; fails=1
    elif grep -q "SLICE2A-RESULT: PASS" "$CONSOLE" 2>/dev/null; then
      echo "  ✓ :9090/:4317、receiver:8000 通；其他 MGMT:8000/:22 不通（nonce 相符）"
    else
      echo "  ✗ 本顆 VM 的開機自驗未通過（見 $CONSOLE 的 SLICE2A-RESULT）"; fails=1
    fi
  fi
fi

# --- 契約 2：MGMT → TARGET 反向不通 -----------------------------------------
echo "=== 契約 2：MGMT → TARGET 反向不通（單向；agent pull 的整個理由）==="
ip netns exec ns-mgmt python3 "$VT" --from-zone mgmt --target "$TARGET" || fails=1

# --- 契約 3：RED → MGMT deny all --------------------------------------------
echo "=== 契約 3：RED → MGMT deny all ==="
if [ "$RED_MODE" = "none" ]; then
  echo "  ✗ 找不到紅隊節點（ns-red1 或 range-red1）"; fails=1
else
  ip netns exec "${RED_NS_PREFIX}1" python3 "$VT" --from-zone red --mgmt "$MGMT" || fails=1
fi

echo "=== Z-APP：HTTPS + exact managed engine API；raw query/target denied ==="
ip netns exec "${RED_NS_PREFIX}1" python3 - "$APP" <<'PY' || fails=1
import socket,sys
for port,want in ((443,True),(22,False)):
 s=socket.socket();s.settimeout(2)
 try:s.connect((sys.argv[1],port));got=True
 except OSError:got=False
 finally:s.close()
 assert got is want, (port,got,want)
PY
if [ "$TARGET_MODE" = "netns" ]; then
ip netns exec ns-target python3 - "$APP" <<'PY' || fails=1
import socket,sys
for port,want in ((443,True),(22,False)):
 s=socket.socket();s.settimeout(2)
 try:s.connect((sys.argv[1],port));got=True
 except OSError:got=False
 finally:s.close()
 assert got is want, (port,got,want)
PY
else
  echo "  ✓ TARGET VM→APP:443 通且 :22 不通（boot nonce marker）"
fi
ip netns exec ns-mgmt python3 - "$APP" <<'PY' || fails=1
import socket,sys
for port,want in ((443,True),(22,False)):
 s=socket.socket();s.settimeout(2)
 try:s.connect((sys.argv[1],port));got=True
 except OSError:got=False
 finally:s.close()
 assert got is want, (port,got,want)
PY
ip netns exec ns-app python3 "$VT" --from-zone app --app "$APP" \
  --mgmt "$MGMT" --engine "$ENGINE" --target "$TARGET" || fails=1

echo "=== 契約 5：EDGE narrow proxy paths；MGMT/TARGET denied ==="
ip netns exec ns-internet python3 "$VT" --from-zone internet --edge "$EDGE" \
  --app "$APP" --red-peer "$RED_IP_FIRST" --blue "$BLUE" --mgmt "$MGMT" \
  --target "$TARGET" || fails=1
ip netns exec ns-edge python3 "$VT" --from-zone edge --edge "$EDGE" \
  --app "$APP" --red-peer "$RED_IP_FIRST" --blue "$BLUE" --mgmt "$MGMT" \
  --target "$TARGET" || fails=1
ip netns exec "${RED_NS_PREFIX}1" python3 - "$EDGE" <<'PY' || fails=1
import socket,sys
s=socket.socket();s.settimeout(2)
try:s.connect((sys.argv[1],443));raise SystemExit("RED→EDGE unexpectedly reachable")
except OSError:pass
finally:s.close()
PY

echo "=== Z-RED seats：預設 L2 隔離 ==="
ip netns exec "${RED_NS_PREFIX}1" python3 "$VT" --from-zone red-seat \
  --red-peer "$RANGE_NET_PREFIX.$Z_RED_VLAN.$((RED_HOST_FIRST + 1))" || fails=1

# 藍隊每人一段是計分歸屬的單位（#65 決策 25），碰得到別人那段就等於碰得到別人的分數來源。
echo "=== Z-BLUE seats：預設 L2 隔離 ==="
ip netns exec ns-blue python3 "$VT" --from-zone blue-seat \
  --blue-peer "$BLUE_PEER_IP" || fails=1

# --- 六台 red source IP 可分辨 ----------------------------------------------
echo "=== 六台 red source IP 可分辨（§12.3 red→target:80；防 G0 的 SNAT 塌縮）==="
REC="/tmp/range-target-src.txt"
if [ "$RED_MODE" = "none" ]; then
  echo "  ✗ 無紅隊節點，略過"; fails=1
else
  if [ "$TARGET_MODE" = "netns" ]; then
    # netns 模式：build-range 已起 listener；這裡只清本輪記錄。
    : > "$REC"
    LPID=""
  else
    LPID=""   # VM 模式：靶機 app 自己會記 source_ip，稍後從它的 log 取
  fi

  # 六台各打一次（probe_target_from_red 的說明見檔頭）。
  for i in $(seq 1 "$RED_COUNT"); do
    probe_target_from_red "${RED_NS_PREFIX}$i"       || echo "  red$i 連 target:80 失敗（§12.3 應允許）"
  done
  sleep 1
  [ -n "$LPID" ] && { kill "$LPID" 2>/dev/null || true; }

  if [ "$TARGET_MODE" = "vm" ]; then
    # VM 模式：source IP 從**真 Loki** 撈靶機 app 的 log（同時證明 Falco/app 的
    # telemetry 真的走完 TARGET→MGMT→Loki，比 stub listener 更強的證據）。
    #
    # Alloy→Loki 有傳輸延遲：六個 probe 幾乎同時送出，但對應的 log 行不會同時
    # 到齊。舊版一撈到「非空」就 break，實測會撈到只有前一兩個 probe 到位的
    # 半成品，把「還沒到齊」誤判成「被 SNAT 塌縮成一個來源」（#17 real-range
    # 觀測到的間歇性失敗）。改成撈到 >= RED_COUNT 個相異 source_ip 才算數，
    # 撈不齊就繼續重試，重試預算用完才收下當時最完整的結果。
    : > "$REC"
    for _ in $(seq 1 15); do
      python3 - "$LOKI_URL" "$REC" "$RED_COUNT" <<'PY' && break
import json, sys, time, urllib.parse, urllib.request
base, rec, expected = sys.argv[1], sys.argv[2], int(sys.argv[3])
end = time.time(); start = end - 300
q = urllib.parse.urlencode({
    "query": '{app="range-target"}', "start": str(int(start*1e9)),
    "end": str(int(end*1e9)), "limit": "1000", "direction": "backward"})
try:
    with urllib.request.urlopen(f"{base.rstrip('/')}/loki/api/v1/query_range?{q}", timeout=5) as r:
        payload = json.load(r)
except Exception:
    sys.exit(1)
ips = []
for stream in (payload.get("data") or {}).get("result") or []:
    for _ts, line in stream.get("values", []):
        try:
            ip = json.loads(line).get("source_ip")
        except Exception:
            ip = None
        if ip:
            ips.append(ip)
if not ips:
    sys.exit(1)
open(rec, "w", encoding="utf-8").write("\n".join(ips) + "\n")
sys.exit(0 if len(set(ips)) >= expected else 1)
PY
      sleep 2
    done

    # 撈不到就把「斷在哪一段」講清楚。空陣列本身無法分辨三種完全不同的故障，
    # 而三者的修法天差地遠（改 range / 修 Alloy / 修靶機 app），不能讓人用猜的。
    if [ ! -s "$REC" ]; then
      echo "  ── 診斷：靶機 telemetry 沒進 Loki ──"
      python3 - "$LOKI_URL" "$MGMT_LOKI_IP" "$Z_MGMT_VLAN" <<'PY'
import json, sys, time, urllib.parse, urllib.request
base = sys.argv[1].rstrip("/")
loki_ip, mgmt_vlan = sys.argv[2], sys.argv[3]

def count(expr):
    end = time.time(); start = end - 900
    p = urllib.parse.urlencode({"query": expr, "start": str(int(start * 1e9)),
                                "end": str(int(end * 1e9)), "limit": "100",
                                "direction": "backward"})
    try:
        with urllib.request.urlopen(f"{base}/loki/api/v1/query_range?{p}", timeout=5) as r:
            payload = json.load(r)
    except Exception as exc:
        return None, f"查詢失敗：{exc}"
    streams = (payload.get("data") or {}).get("result") or []
    return sum(len(s.get("values", [])) for s in streams), None

app_n, app_err = count('{app="range-target"}')
falco_n, falco_err = count('{job="falco"}')
for label, n, err in (("app=range-target", app_n, app_err), ("job=falco", falco_n, falco_err)):
    print(f"  · {{{label}}} 近 15 分鐘行數：{n if err is None else err}")

if app_err or falco_err:
    print("  → Loki 查不動：先確認 compose 的 loki 有在跑（docker compose ps）")
elif app_n == 0 and falco_n == 0:
    print("  → 靶機 VM 的 Alloy 沒推上來：契約 1（TARGET→MGMT :3100）實用斷了。")
    print(f"    查：VM 內 purplescope-alloy.service 狀態、Loki 是否掛在 VLAN{mgmt_vlan} {loki_ip}")
    print("    （scripts/range/attach-mgmt.sh），以及 golden 是否為新版（.stamp）")
elif app_n == 0:
    print("  → Alloy 通（falco 進得來），但靶機 app 沒寫 log：")
    print("    多半是 red→target:80 沒真的打到，或 range-target-app.service 沒起")
else:
    print("  → app log 有進來但沒有 source_ip 欄位：只有 startup 那行，請求沒被處理")
PY
    fi
  fi

  python3 - "$REC" "$REPO/src" <<'PY' || fails=1
import sys
rec, src_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, src_path)
from purple.topology_check import check_source_ips_distinguishable, EXPECTED_KALI
try:
    ips = [ln.strip() for ln in open(rec, encoding="utf-8") if ln.strip()]
except OSError:
    ips = []
print(f"  target 觀測到的 source IP：{sorted(set(ips))}")
msgs = check_source_ips_distinguishable(ips)
if msgs:
    for m in msgs:
        print("  ✗", m)
    sys.exit(1)
print(f"  ✓ {EXPECTED_KALI} 台 red 各自可分辨，未被 SNAT 塌縮")
PY
fi

if [ "$fails" -ne 0 ]; then
  echo "❌ 有契約未通過"
  exit 1
fi
echo "✅ 契約全通過（真方向性防火牆；靶機=$TARGET_MODE、紅隊=$RED_MODE）"
