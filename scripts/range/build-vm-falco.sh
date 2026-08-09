#!/usr/bin/env bash
# 票 #13 Slice 2b-① —— 證明 Falco（modern-eBPF / CO-RE）能在真 VM 內監控靶機
# 自身的 syscall 並抓到已知動作。這是 #9「Falco 作為 sensor」的**真環境**能力證明
# （目前 #9 只有決定性測試綠、真 Falco 待環境）。
#
# 為什麼走 NAT 而非 VLAN20：Slice 1 的 router 刻意不做 SNAT（保六台 kali source IP
# 可分辨），所以 VLAN20 無對外網路，VM 裝不了 apt 來源的 Falco。2b-① 只證「Falco
# 能力在此 VM kernel 成立」，故用 libvirt default（NAT）取得 internet 裝 Falco。
# isolation 在 Slice 2a 已證；Slice 4 再把 Falco 烤成 golden image 跑在無網的 VLAN20。
#
# 自足驗證、免 SSH：VM serial console 導檔；cloud-init 開機裝 Falco、下一條自訂 rule、
# 觸發 sentinel 檔、從 journald 撈命中，把結果印到 console；host 讀該檔判定。
#
# 需 root + Slice 2 依賴（見 scripts/range/HOST-SETUP.md）。此腳本**不在 CI**：
# GitHub runner 無巢狀虛擬化，真 VM 只能在大主機驗。
# 覆寫點（環境變數）：OSV（os-variant，預設 ubuntu24.04）、VM_MEM、VM_VCPUS。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/range/lib-cloudimg.sh
source "$DIR/lib-cloudimg.sh"

VM="range-falco-smoke"
IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
CACHE="/var/lib/libvirt/images"
BASE="$CACHE/noble-cloudimg.img"
DISK="$CACHE/$VM.qcow2"
CONSOLE="/tmp/range-falco-console.log"
OSV="${OSV:-ubuntu24.04}"
VM_MEM="${VM_MEM:-3072}"   # Falco + apt，比 2a 寬一點
VM_VCPUS="${VM_VCPUS:-2}"

echo "▶ 確保 libvirt default（NAT）網路已起（Falco 裝機要 internet）"
virsh net-start default 2>/dev/null || true
virsh net-autostart default 2>/dev/null || true

echo "▶ 取 Ubuntu cloud image（快取於 $BASE）"
fetch_cloudimg "$IMG_URL" "$BASE"

echo "▶ 清掉舊的 falco smoke VM（冪等）"
virsh destroy "$VM" 2>/dev/null || true
virsh undefine "$VM" --nvram 2>/dev/null || true
rm -f "$DISK" "$CONSOLE"

echo "▶ 建 overlay disk（不動原 image）"
qemu-img create -f qcow2 -F qcow2 -b "$BASE" "$DISK" 10G >/dev/null

echo "▶ 產生 cloud-init（開機裝 Falco modern-eBPF + 自驗抓到已知動作）"
# 關鍵：邏輯放進 write_files 的 /opt/falco-smoke.sh（#!/bin/bash），runcmd 只呼叫它。
# 不能把邏輯直接寫進 runcmd —— cloud-init 用 /bin/sh(dash) 跑 runcmd，process
# substitution >(...) 這種 bashism 會 "redirection unexpected" 整段不執行。
UD="$(mktemp)"; NC="$(mktemp)"
cat > "$UD" <<'YAML'
#cloud-config
package_update: false
write_files:
  - path: /etc/falco/rules.d/purplescope.yaml
    content: |
      # 2b-① smoke：讀取 sentinel 檔就開火。條件自足、不依賴預設 macro，
      # 免得預設 ruleset 改版影響這條測試。fd.name 在 syscall 退出（dir=<）才解析。
      - rule: PurpleScope Test Trigger
        desc: 2b-1 smoke, fires when the sentinel file is opened for read
        condition: (evt.type=open or evt.type=openat or evt.type=openat2) and evt.dir=< and fd.name=/tmp/purplescope-trigger
        output: "PURPLESCOPE-FALCO-HIT proc=%proc.name file=%fd.name"
        priority: WARNING
        tags: [purplescope, test]
  - path: /opt/falco-smoke.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      exec > >(tee /dev/console) 2>&1
      set -x
      echo "=== SLICE2B1-BEGIN（真 VM 內裝 Falco 並自驗）==="
      export DEBIAN_FRONTEND=noninteractive

      echo "--- 加 Falco 官方 apt 來源並安裝 ---"
      curl -fsSL https://falco.org/repo/falcosecurity-packages.asc -o /usr/share/keyrings/falcosecurity.asc
      echo "deb [signed-by=/usr/share/keyrings/falcosecurity.asc] https://download.falco.org/packages/deb stable main" > /etc/apt/sources.list.d/falcosecurity.list
      apt-get update -q
      apt-get install -y -q falco
      echo "falco 版本：$(falco --version 2>/dev/null | head -1)"

      echo "--- 起 Falco modern-eBPF service（吃我們的自訂 rule）---"
      touch /tmp/purplescope-trigger
      systemctl restart falco-modern-bpf.service
      sleep 2
      echo "falco-modern-bpf 狀態：$(systemctl is-active falco-modern-bpf.service)"

      echo "--- 邊觸發邊等命中（最多 40 秒；涵蓋 eBPF 掛載時間）---"
      HIT=0
      for i in $(seq 1 40); do
        cat /tmp/purplescope-trigger > /dev/null 2>&1
        sleep 1
        if journalctl -u falco-modern-bpf.service --no-pager 2>/dev/null | grep -q PURPLESCOPE-FALCO-HIT; then
          HIT=1; break
        fi
      done

      if [ "$HIT" = 1 ]; then
        echo "命中證據："
        journalctl -u falco-modern-bpf.service --no-pager | grep PURPLESCOPE-FALCO-HIT | tail -1
        echo "=== SLICE2B1-RESULT: PASS ==="
      else
        echo "--- 未命中，falco service 最後 25 行 ---"
        journalctl -u falco-modern-bpf.service --no-pager | tail -25
        echo "=== SLICE2B1-RESULT: FAIL ==="
      fi
runcmd:
  - [bash, /opt/falco-smoke.sh]
YAML
cat > "$NC" <<'YAML'
version: 2
ethernets:
  nat:
    match: {name: "e*"}
    dhcp4: true
YAML

echo "▶ 建 Falco smoke VM（接 libvirt default / NAT，取得 internet 裝 Falco）"
virt-install --name "$VM" --memory "$VM_MEM" --vcpus "$VM_VCPUS" \
  --disk path="$DISK",format=qcow2 --import \
  --os-variant "$OSV" \
  --network network=default,model=virtio \
  --cloud-init user-data="$UD",network-config="$NC" \
  --serial file,path="$CONSOLE" \
  --graphics none --noautoconsole

echo "▶ 等 VM 開機 + cloud-init 裝 Falco + 自驗（裝機走 internet，約 2–5 分鐘）..."
ok=0
for _ in $(seq 1 120); do   # 120×3s = 360s
  if grep -q "SLICE2B1-RESULT" "$CONSOLE" 2>/dev/null; then ok=1; break; fi
  sleep 3
done

echo "--- Slice 2b-① 結果（真 VM 內的 Falco 自驗）---"
if [ "$ok" = 1 ]; then
  grep "SLICE2B1" "$CONSOLE" || true
else
  echo "❌ 沒等到結果（360s）。看完整 console：sudo cat $CONSOLE"
  exit 1
fi

if grep -q "SLICE2B1-RESULT: PASS" "$CONSOLE"; then
  echo "✅ Slice 2b-①：Falco（modern-eBPF）在真 VM 內監控靶機 syscall，抓到已知動作"
  echo "   下一步 2b-②：把 Falco 事件接上 Alloy → Loki → Core Event（#9 真環境驗收）"
else
  echo "❌ Falco 未抓到（見上方 falco service log）"
  exit 1
fi
