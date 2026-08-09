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
# Alloy 預設以 alloy 使用者跑，讀不到 root 寫的 falco events.json；範圍是實驗靶機，
# 直接以 root 跑最省事且不會靜默漏事件。
mkdir -p /etc/systemd/system/alloy.service.d
printf '[Service]\nUser=root\nGroup=root\n' > /etc/systemd/system/alloy.service.d/override.conf

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
for u in falco-modern-bpf.service alloy.service range-target-app.service; do
  systemctl enable "$u" || echo "!! enable $u 失敗"
done

echo "--- 5/5 就地驗一次（有網、kernel 6.8）---"
for u in falco-modern-bpf.service range-target-app.service alloy.service; do
  systemctl start "$u" || echo "!! start $u 失敗"
done
sleep 10
FALCO_STATE=$(systemctl is-active falco-modern-bpf.service)
ALLOY_STATE=$(systemctl is-active alloy.service)
APP_STATE=$(systemctl is-active range-target-app.service)
echo "=== GOLDEN-FALCO-STATE: $FALCO_STATE ==="
echo "=== GOLDEN-ALLOY-STATE: $ALLOY_STATE ==="
echo "=== GOLDEN-APP-STATE: $APP_STATE ==="

# 任一沒起來就把真因印到 console —— 這顆 VM 沒有 SSH，console 是唯一的窗口，
# 少印一次就要多花一輪 5 分鐘重烤。
diagnose() {
  local unit="$1"
  echo "---- 診斷 $unit ----"
  systemctl status "$unit" --no-pager -l 2>&1 | tail -15
  journalctl -u "$unit" --no-pager 2>/dev/null | tail -25
}
if [ "$FALCO_STATE" != active ]; then
  diagnose falco-modern-bpf.service
  echo "---- falco 設定與規則 ----"
  ls -l /etc/falco/config.d/ /etc/falco/rules.d/ 2>&1
  echo "[設定內容]"; cat /etc/falco/config.d/purplescope.yaml
  echo "[falco 自我檢查]"; falco --dry-run -c /etc/falco/falco.yaml 2>&1 | tail -15
fi
if [ "$ALLOY_STATE" != active ]; then
  diagnose alloy.service
  echo "---- alloy 設定 ----"
  ls -l /etc/alloy/ 2>&1
  alloy fmt /etc/alloy/config.alloy 2>&1 | tail -15
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
systemctl stop alloy.service falco-modern-bpf.service range-target-app.service || true
rm -f /var/log/falco/events.json /var/log/range-target/app.log
cloud-init clean --logs
poweroff
