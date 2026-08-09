#!/usr/bin/env bash
# 票 #13 Slice 4 —— 產出「Falco 已烤進」的 golden 靶機 image。
#
# 為什麼要 golden：VLAN20 刻意無對外網（保六台 kali source IP 可分辨），靶機在
# VLAN20 上裝不了 apt 來源的 Falco。所以先在**有網的 NAT** 下把 Falco（modern-eBPF）
# 裝進 image、enable falco-modern-bpf.service，關機，把該 disk 轉成 golden；之後
# range-up --with-falco 用它當 base 跑在無網 VLAN20，開機 Falco 自動就位。
#
# 這一步是 2a（真 VM 接 VLAN20）+ 2b-①（Falco 在 VM 內）的合體最終形態。
# 需 root + Slice 2 依賴。**不在 CI**（無巢狀虛擬化）。
# 覆寫點：OSV、VM_MEM、VM_VCPUS。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/range/lib-cloudimg.sh
source "$DIR/lib-cloudimg.sh"

VM="range-golden-build"
IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
CACHE="/var/lib/libvirt/images"
BASE="$CACHE/noble-cloudimg.img"
BUILD="$CACHE/$VM.qcow2"
GOLDEN="$CACHE/range-target-golden.qcow2"
CONSOLE="/tmp/range-golden-console.log"
OSV="${OSV:-ubuntu24.04}"
VM_MEM="${VM_MEM:-3072}"
VM_VCPUS="${VM_VCPUS:-2}"

echo "▶ 確保 libvirt default（NAT）網路已起（烤 Falco 要 internet）"
virsh net-start default 2>/dev/null || true
virsh net-autostart default 2>/dev/null || true

echo "▶ 取 Ubuntu cloud image"
fetch_cloudimg "$IMG_URL" "$BASE"

echo "▶ 清掉舊 build VM / 舊 golden（冪等）"
virsh destroy "$VM" 2>/dev/null || true
virsh undefine "$VM" --nvram 2>/dev/null || true
rm -f "$BUILD" "$GOLDEN" "$CONSOLE"

echo "▶ 建 build overlay（之後轉成獨立 golden）"
qemu-img create -f qcow2 -F qcow2 -b "$BASE" "$BUILD" 10G >/dev/null

echo "▶ 產生 cloud-init（裝 Falco modern-eBPF、enable service、下 rule，然後關機）"
UD="$(mktemp)"
cat > "$UD" <<'YAML'
#cloud-config
package_update: false
write_files:
  - path: /etc/falco/rules.d/purplescope.yaml
    content: |
      - rule: PurpleScope Command Exec
        desc: T1059 smoke - a scripting interpreter was spawned with the purplescope marker
        condition: >
          evt.type=execve and evt.dir=< and
          proc.name in (sh, bash, dash, ash) and
          proc.cmdline contains PURPLESCOPE_EXEC
        output: "PurpleScope exec detected (cmd=%proc.cmdline pid=%proc.pid parent=%proc.pname)"
        priority: WARNING
        tags: [purplescope, T1059, execution]
  - path: /opt/bake-falco.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      exec > >(tee /dev/console) 2>&1
      set -x
      echo "=== GOLDEN-BAKE-BEGIN（裝 Falco 進 image）==="
      export DEBIAN_FRONTEND=noninteractive
      curl -fsSL https://falco.org/repo/falcosecurity-packages.asc -o /usr/share/keyrings/falcosecurity.asc
      echo "deb [signed-by=/usr/share/keyrings/falcosecurity.asc] https://download.falco.org/packages/deb stable main" > /etc/apt/sources.list.d/falcosecurity.list
      apt-get update -q
      apt-get install -y -q falco
      # 讓 modern-bpf service 開機自動起（golden 在無網 VLAN20 靠它）。
      systemctl enable falco-modern-bpf.service
      echo "falco 版本：$(falco --version 2>/dev/null | head -1)"
      echo "=== GOLDEN-BAKE-DONE ==="
      # 關機：讓 host 知道烤完，之後把 disk 轉 golden。
      poweroff
runcmd:
  - [bash, /opt/bake-falco.sh]
YAML

echo "▶ 起 build VM（NAT），跑烤 Falco 後自動關機"
virt-install --name "$VM" --memory "$VM_MEM" --vcpus "$VM_VCPUS" \
  --disk path="$BUILD",format=qcow2 --import \
  --os-variant "$OSV" \
  --network network=default,model=virtio \
  --cloud-init user-data="$UD" \
  --serial file,path="$CONSOLE" \
  --graphics none --noautoconsole

echo "▶ 等烤完 + VM 關機（裝機走 internet，約 3–6 分鐘）..."
ok=0
for _ in $(seq 1 140); do   # 140×3s = 420s
  state="$(virsh domstate "$VM" 2>/dev/null || echo unknown)"
  if grep -q "GOLDEN-BAKE-DONE" "$CONSOLE" 2>/dev/null && [ "$state" = "shut off" ]; then
    ok=1; break
  fi
  sleep 3
done

if [ "$ok" != 1 ]; then
  echo "❌ 沒等到烤完+關機。看 console：sudo cat $CONSOLE"
  exit 1
fi
grep -E "GOLDEN-BAKE|falco 版本" "$CONSOLE" || true

echo "▶ 把烤好的 disk 轉成獨立 golden image（$GOLDEN）"
# convert 攤平 backing chain → golden 自足，可當新 base。
qemu-img convert -O qcow2 "$BUILD" "$GOLDEN"
virsh undefine "$VM" --nvram 2>/dev/null || true
rm -f "$BUILD"

echo "✅ Golden image 就緒：$GOLDEN（Falco modern-eBPF 已烤進、service 已 enable）"
echo "   用法：range-up.sh --with-falco （會以此為 base 在無網 VLAN20 起靶機）"
