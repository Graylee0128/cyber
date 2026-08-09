#!/bin/bash
# 在 golden build VM 內執行（由 cloud-init runcmd 呼叫）—— 把靶機該有的東西烤進 image：
#   Falco（modern-eBPF，runtime sensor）＋ Alloy（target 側 collector）＋ 靶機 app(:80)
#   ＋ 敏感檔（SA §7 Scenario 03 的標的）
# 最後 cloud-init clean + poweroff，host 再把 disk 轉成 golden。
#
# 這支跑在**有網的 NAT**（VLAN20 無外網，裝不了套件）；golden 之後才上 VLAN20。
exec > >(tee /dev/console) 2>&1
set -x
echo "=== GOLDEN-BAKE-BEGIN（烤 Falco + Alloy + 靶機 app）==="
export DEBIAN_FRONTEND=noninteractive

echo "--- 1/5 Falco（runtime sensor）---"
curl -fsSL https://falco.org/repo/falcosecurity-packages.asc -o /usr/share/keyrings/falcosecurity.asc
echo "deb [signed-by=/usr/share/keyrings/falcosecurity.asc] https://download.falco.org/packages/deb stable main" \
  > /etc/apt/sources.list.d/falcosecurity.list
apt-get update -q
apt-get install -y -q falco
echo "falco 版本：$(falco --version 2>/dev/null | head -1)"

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
curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
  > /etc/apt/sources.list.d/grafana.list
apt-get update -q
apt-get install -y -q alloy

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

echo "--- 3/5 靶機 app（:80 /exec /readsecret）---"
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
echo "[裝好的相關 unit]"
systemctl list-unit-files 2>/dev/null | grep -iE 'falco|alloy' || echo "(找不到 falco/alloy unit)"
for u in falco-modern-bpf.service purplescope-alloy.service range-target-app.service; do
  systemctl enable "$u" || echo "!! enable $u 失敗"
done

echo "--- 5/5 就地驗一次（有網、kernel 6.8）---"
for u in falco-modern-bpf.service range-target-app.service purplescope-alloy.service; do
  systemctl start "$u" || echo "!! start $u 失敗"
done
sleep 10
FALCO_STATE=$(systemctl is-active falco-modern-bpf.service)
ALLOY_STATE=$(systemctl is-active purplescope-alloy.service)
APP_STATE=$(systemctl is-active range-target-app.service)
echo "=== GOLDEN-FALCO-STATE: $FALCO_STATE ==="
echo "=== GOLDEN-ALLOY-STATE: $ALLOY_STATE ==="
echo "=== GOLDEN-APP-STATE: $APP_STATE ==="

# 任一沒起來就把真因印到 console —— 這顆 VM 沒有 SSH，console 是唯一的窗口，
# 少印一次就要多花一輪 5 分鐘重烤。
# 每行都加 DIAG| 前綴：關機序列有上百行，host 端才能精準撈出診斷而不是被淹掉。
diag() { sed 's/^/DIAG| /'; }
diagnose() {
  local unit="$1"
  echo "DIAG| ---- 診斷 $unit ----"
  systemctl status "$unit" --no-pager -l 2>&1 | tail -15 | diag
  journalctl -u "$unit" --no-pager 2>/dev/null | grep -iE "error|fatal|failed|panic" | head -15 | diag
  journalctl -u "$unit" --no-pager 2>/dev/null | tail -15 | diag
}
if [ "$FALCO_STATE" != active ]; then
  diagnose falco-modern-bpf.service
  echo "DIAG| ---- falco 設定與規則 ----"
  { ls -l /etc/falco/config.d/ /etc/falco/rules.d/; cat /etc/falco/config.d/purplescope.yaml; } 2>&1 | diag
fi
if [ "$ALLOY_STATE" != active ]; then
  diagnose purplescope-alloy.service
  echo "DIAG| ---- alloy 設定與前景實跑（把解析錯誤逼出來）----"
  { ls -l /etc/alloy/; echo "[config 前 5 行]"; head -5 /etc/alloy/config.alloy; } 2>&1 | diag
  # 前景跑一次：unit 的 stdout 有時被吞，直接跑最能拿到真正的錯誤訊息。
  timeout 15 alloy run /etc/alloy/config.alloy --storage.path=/tmp/alloy-probe 2>&1 | tail -25 | diag
fi

# 就地觸發一次，確認 Falco 真的抓得到我們的兩條 rule（bake 期自證，不必等上線才發現）。
curl -s -m 5 http://127.0.0.1/exec       >/dev/null || true
curl -s -m 5 http://127.0.0.1/readsecret >/dev/null || true
sleep 5
EXEC_HITS=$(grep -c "PurpleScope exec detected" /var/log/falco/events.json 2>/dev/null || echo 0)
SEC_HITS=$(grep -c "PurpleScope sensitive file access" /var/log/falco/events.json 2>/dev/null || echo 0)
echo "=== GOLDEN-RULE-HITS: exec=$EXEC_HITS secret=$SEC_HITS ==="

echo "=== GOLDEN-BAKE-DONE ==="

# golden image 標準收尾：
#  - 清掉 bake 期產生的 log/事件，image 才乾淨（上線後的事件才是演練資料）
#  - cloud-init clean：讓下次以此為 base 開機時，build-vm-target 的靜態 IP + 契約
#    cloud-init 會**重跑**（否則被當成同一 instance 直接略過）
systemctl stop purplescope-alloy.service falco-modern-bpf.service range-target-app.service || true
rm -f /var/log/falco/events.json /var/log/range-target/app.log
cloud-init clean --logs
poweroff
