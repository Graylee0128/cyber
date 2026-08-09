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
systemctl enable falco-modern-bpf.service alloy.service range-target-app.service

echo "--- 5/5 就地驗一次（有網、kernel 6.8）---"
systemctl start falco-modern-bpf.service || true
systemctl start range-target-app.service || true
systemctl start alloy.service || true
sleep 10
echo "=== GOLDEN-FALCO-STATE: $(systemctl is-active falco-modern-bpf.service) ==="
echo "=== GOLDEN-ALLOY-STATE: $(systemctl is-active alloy.service) ==="
echo "=== GOLDEN-APP-STATE: $(systemctl is-active range-target-app.service) ==="

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
