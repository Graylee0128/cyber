#!/bin/bash
# 在 golden build VM 內執行（由 cloud-init runcmd 呼叫）—— 把靶機該有的東西烤進 image：
#   Falco（modern-eBPF，runtime sensor）＋ Alloy（target 側 collector）＋ 靶機 app(:80)
#   ＋ 敏感檔（SA §7 Scenario 03 的標的）
# 最後 cloud-init clean + poweroff，host 再把 disk 轉成 golden。
#
# 這支跑在**有網的 NAT**（VLAN20 無外網，裝不了套件）；golden 之後才上 VLAN20。
#
# 錯誤處理（2026-08-09 code review）：原本整支只有 `set -x`，curl 抓 key、apt 裝
# Falco/Alloy 失敗了都照樣往下跑，真因要三分鐘後以「ALLOY-STATE: failed」這種粗網目
# 才浮現。現在改 `set -Eeuo pipefail` + ERR trap：哪一行死的當場講清楚。
# 不能只加 `set -e` —— 那會跳過結尾的 poweroff，host 端白等 600s 逾時；所以 trap 裡
# 一定要印 GOLDEN-BAKE-ABORT（host 用它提早結束等待）並自己關機。
set -Eeuo pipefail

exec > >(tee /dev/console) 2>&1
set -x

# 這顆 VM 要跑的三個 service —— 一處定義，enable / start / 檢查 / 收尾都用它，
# 免得日後加第四個（例如票 #17 的 response agent）要改四個地方。
UNITS=(falco-modern-bpf.service purplescope-alloy.service range-target-app.service)

# 診斷本身不該因為「被診斷的東西是壞的」而中止腳本，所以整段關掉 errexit。
# 每行都加 DIAG| 前綴：關機序列有上百行，host 端才能精準撈出診斷而不是被淹掉。
diag() { sed 's/^/DIAG| /'; }
diagnose() {
  local unit="$1"
  set +e
  echo "DIAG| ---- 診斷 $unit ----"
  systemctl status "$unit" --no-pager -l 2>&1 | tail -15 | diag
  journalctl -u "$unit" --no-pager 2>/dev/null | grep -iE "error|fatal|failed|panic" | head -15 | diag
  journalctl -u "$unit" --no-pager 2>/dev/null | tail -15 | diag
  set -e
}

# 任何未預期的失敗都走這裡：講清楚死在哪一行，然後**一定要關機**。
#
# 只印**死因相關**的東西，不印各 unit 的 journal（2026-08-10 實測教訓）：
# 那次死在 `apt-get install alloy`，但 handler 順手把 falco 的 journal 也倒出來，
# 幾十行把 apt 的真正錯誤從 host 端的 `tail` 視窗擠了出去 —— 診斷反而看不到死因。
# 「指令為什麼死」與「服務為什麼沒起來」是兩個問題，各自有各自的出口：
# 前者在這裡，後者在第 5 步的 STATE 檢查裡呼叫 diagnose。
on_error() {
  local rc=$? line=$1
  trap - ERR                 # 防止 handler 內再觸發自己
  set +e
  echo "=== GOLDEN-BAKE-ABORT: bake.sh 第 $line 行失敗（exit $rc）==="
  echo "DIAG| ---- 出事的那一行 ----"
  sed -n "$((line > 3 ? line - 3 : 1)),$((line + 1))p" "$0" 2>/dev/null | diag
  echo "DIAG| ---- 磁碟／記憶體 ----"
  { df -h /; free -m; } 2>&1 | diag
  echo "DIAG| ---- 對外連線（apt/pip 逾時多半是這裡）----"
  { getent hosts apt.grafana.com download.falco.org archive.ubuntu.com; \
    timeout 5 curl -sSI -o /dev/null -w 'apt.grafana.com → HTTP %{http_code} in %{time_total}s\n' \
      https://apt.grafana.com/gpg.key; } 2>&1 | diag
  # apt 的真正錯誤放**最後**：host 端用 tail 取，最後印的最不會被截掉。
  echo "DIAG| ---- apt 最後的輸出（死因通常在這裡）----"
  { tail -40 /var/log/apt/term.log; tail -15 /var/log/apt/history.log; } 2>&1 | diag
  echo "=== GOLDEN-BAKE-DONE（ABORT）==="
  poweroff -f
}
trap 'on_error $LINENO' ERR

# apt 也要禁得起網路抖動（同 Dockerfile 的 pip --retries，2026-08-10 同一台實測）：
# 預設不重試，一次連線失敗就 exit 100，整顆 golden 白烤 6 分鐘。
APT_OPTS=(-o Acquire::Retries=5
          -o Acquire::http::Timeout=60
          -o Acquire::https::Timeout=60)

echo "=== GOLDEN-BAKE-BEGIN（烤 Falco + Alloy + 靶機 app）==="
export DEBIAN_FRONTEND=noninteractive

echo "--- 1/5 Falco（runtime sensor）---"
CURL=(curl -fsSL --retry 5 --retry-all-errors --connect-timeout 15 --max-time 120)
"${CURL[@]}" -o /usr/share/keyrings/falcosecurity.asc https://falco.org/repo/falcosecurity-packages.asc
echo "deb [signed-by=/usr/share/keyrings/falcosecurity.asc] https://download.falco.org/packages/deb stable main" \
  > /etc/apt/sources.list.d/falcosecurity.list
apt-get "${APT_OPTS[@]}" update -q
apt-get "${APT_OPTS[@]}" install -y -q falco
echo "falco 版本：$(falco --version 2>/dev/null | head -1 || true)"

# Falco 設定：JSON + 寫檔給 Alloy tail。自訂 rule 已放進 /etc/falco/rules.d（預設會載入該目錄）。
mkdir -p /var/log/falco /etc/falco/config.d
cat > /etc/falco/config.d/purplescope.yaml <<'CFG'
json_output: true
file_output:
  enabled: true
  keep_alive: false
  filename: /var/log/falco/events.json
CFG

echo "--- 2/5 Alloy（target 側 collector）---"
mkdir -p /etc/apt/keyrings
"${CURL[@]}" https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
  > /etc/apt/sources.list.d/grafana.list
apt-get "${APT_OPTS[@]}" update -q
apt-get "${APT_OPTS[@]}" install -y -q alloy

# 不用套件附的 alloy.service —— 它在安裝時就自動啟動，且帶一整套 vendor 參數與
# hardening；實測（2026-08-09）它會連續崩到觸發 systemd 重啟上限
# （Start request repeated too quickly / result 'resources'），但**同一份設定用前景
# 指令跑完全正常**。與其逐項猜 vendor unit 哪個選項不合，不如自己寫 unit：
# 跑的就是已驗證可行的那條指令，行為完全在我們掌握內。
systemctl disable --now alloy.service 2>/dev/null || true
systemctl mask alloy.service 2>/dev/null || true

mkdir -p /var/lib/purplescope-alloy
cat > /etc/systemd/system/purplescope-alloy.service <<'UNIT'
[Unit]
Description=PurpleScope Alloy collector (target side)
After=network-online.target
Wants=network-online.target

[Service]
# 以 root 跑：要讀 root 權限的 /var/log/falco/events.json，實驗靶機不值得為此做群組權限設計。
User=root
Group=root
ExecStart=/usr/bin/alloy run /etc/alloy/config.alloy --storage.path=/var/lib/purplescope-alloy
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

echo "--- 3/5 靶機 app（:80 /exec /readsecret /uncovered）---"
mkdir -p /var/log/range-target
cat > /etc/systemd/system/range-target-app.service <<'UNIT'
[Unit]
Description=PurpleScope range target app
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 -u /opt/range-target/app.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

echo "--- 4/5 enable 服務（golden 開機後自動就位，無網也能跑）---"
systemctl daemon-reload
# 逐個 enable：一次列多個時只要有一個 unit 名不存在，整條指令失敗、其餘也沒 enable 到。
# 這裡刻意不讓 enable/start 失敗直接中止 —— 下面的 STATE 檢查會逐個判並印診斷，
# 那比死在第一個失敗的 unit 上更有資訊量。
echo "[裝好的相關 unit]"
systemctl list-unit-files 2>/dev/null | grep -iE 'falco|alloy' || echo "(找不到 falco/alloy unit)"
for u in "${UNITS[@]}"; do
  systemctl enable "$u" || echo "!! enable $u 失敗"
done

echo "--- 5/5 就地驗一次（有網、kernel 6.8）---"
for u in "${UNITS[@]}"; do
  systemctl start "$u" || echo "!! start $u 失敗"
done
sleep 10

# `systemctl is-active` 在服務沒起來時回非 0 —— 沒有 `|| true` 會被 errexit 當成
# 腳本失敗，反而拿不到我們想印的狀態與診斷。
bake_fail=0
for u in "${UNITS[@]}"; do
  state="$(systemctl is-active "$u" || true)"
  # unit 名 → 標籤：falco-modern-bpf → FALCO、purplescope-alloy → ALLOY、range-target-app → APP
  case "$u" in
    falco*)       label=FALCO ;;
    *alloy*)      label=ALLOY ;;
    *target-app*) label=APP ;;
    *)            label="${u%%.*}" ;;
  esac
  echo "=== GOLDEN-$label-STATE: $state ==="
  if [ "$state" != active ]; then
    bake_fail=1
    diagnose "$u"
  fi
done

# 沒起來時把設定也印出來 —— 這顆 VM 沒有 SSH，console 是唯一的窗口，
# 少印一次就要多花一輪 5 分鐘重烤。
if [ "$bake_fail" != 0 ]; then
  set +e
  echo "DIAG| ---- falco 設定與規則 ----"
  { ls -l /etc/falco/config.d/ /etc/falco/rules.d/; cat /etc/falco/config.d/purplescope.yaml; } 2>&1 | diag
  echo "DIAG| ---- alloy 設定與前景實跑（把解析錯誤逼出來）----"
  { ls -l /etc/alloy/; echo "[config 前 5 行]"; head -5 /etc/alloy/config.alloy; } 2>&1 | diag
  # 前景跑一次：unit 的 stdout 有時被吞，直接跑最能拿到真正的錯誤訊息。
  timeout 15 alloy run /etc/alloy/config.alloy --storage.path=/tmp/alloy-probe 2>&1 | tail -25 | diag
  set -e
fi

# 就地觸發一次，確認 Falco 真的抓得到我們的三條 rule（bake 期自證，不必等上線才發現）。
# /uncovered 也在這裡打一次：它的 Falco 規則存在但**刻意沒有 Grafana 規則**，
# 決定性測試靠它區分「看得到卻沒偵測到」與「根本沒看到」（ADR ③）。
for ep in /exec /readsecret /uncovered; do
  curl -s -m 5 "http://127.0.0.1$ep" >/dev/null || true
done
sleep 5
EXEC_HITS=$(grep -c "PurpleScope exec detected" /var/log/falco/events.json 2>/dev/null || echo 0)
SEC_HITS=$(grep -c "PurpleScope sensitive file access" /var/log/falco/events.json 2>/dev/null || echo 0)
UNCOV_HITS=$(grep -c "PurpleScope uncovered action" /var/log/falco/events.json 2>/dev/null || echo 0)
echo "=== GOLDEN-RULE-HITS: exec=$EXEC_HITS secret=$SEC_HITS uncovered=$UNCOV_HITS ==="

echo "=== GOLDEN-BAKE-DONE ==="

# golden image 標準收尾：
#  - 清掉 bake 期產生的 log/事件，image 才乾淨（上線後的事件才是演練資料）
#  - cloud-init clean：讓下次以此為 base 開機時，build-vm-target 的靜態 IP + 契約
#    cloud-init 會**重跑**（否則被當成同一 instance 直接略過）
systemctl stop "${UNITS[@]}" || true
rm -f /var/log/falco/events.json /var/log/range-target/app.log
cloud-init clean --logs
poweroff
