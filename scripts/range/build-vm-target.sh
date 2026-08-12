#!/usr/bin/env bash
# 票 #13 Slice 2a —— 靶機真 VM（KVM/libvirt）接 OVS 的 VLAN20，驗契約 1（從真 VM）
# 與契約 2（從 mgmt netns 連 VM，應不通）。這是 netns → 真 VM 的關鍵跳躍（混合模式）。
#
# 自足驗證、免 SSH：VM 的 serial console 導到檔案，cloud-init 開機時跑契約 1 並把
# 結果印到 console；host 讀該檔判定。契約 2 由 host 從 ns-mgmt 跑 verify_topology。
#
# 需 root + Slice 2 依賴（見 scripts/range/HOST-SETUP.md）。此腳本**不在 CI**：
# GitHub runner 無巢狀虛擬化，真 VM 只能在大主機驗。
#
# 覆寫點（環境變數）：OSV（os-variant，預設 ubuntu24.04）、VM_MEM、VM_VCPUS。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"

VM="range-target"
IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
CACHE="/var/lib/libvirt/images"
# 預設用乾淨 cloud image；range-up --with-falco 會傳 BASE_OVERRIDE=golden（Falco 已烤進）。
BASE="${BASE_OVERRIDE:-$CACHE/noble-cloudimg.img}"
DISK="$CACHE/$VM.qcow2"
CONSOLE="/tmp/range-target-console.log"
NONCE_FILE="/tmp/range-target-boot.nonce"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"   # TARGET_IP / MGMT_STUB_IP / Z_TARGET_GW 等位址的唯一定義處
MGMT_IP="$MGMT_STUB_IP"
RESPONSE_IP="$MGMT_RECEIVER_IP"
APP_IP="$APP_STUB_IP"
OSV="${OSV:-ubuntu24.04}"
VM_MEM="${VM_MEM:-2048}"
VM_VCPUS="${VM_VCPUS:-2}"

echo "▶ 建 OVS 六區骨架（target 留給 VM）"
SKIP_TARGET_NETNS=1 bash "$DIR/build-range.sh"

echo "▶ 定義 libvirt 接 OVS 的 network（range-ovs，冪等）"
if ! virsh net-info range-ovs >/dev/null 2>&1; then
  virsh net-define "$DIR/range-ovs.xml"
fi
virsh net-start range-ovs 2>/dev/null || true
virsh net-autostart range-ovs 2>/dev/null || true

echo "▶ 取 Ubuntu cloud image（快取於 $BASE）"
# cloud image 是 qcow2；qemu-img check 通過才算完整（0=好、3=只是 leak 也可用）。
# 這道把關擋掉「半截下載」的損毀 image——否則 overlay 會讀到壞區塊，VM 開機直接
# EXT4 I/O error → kernel panic（查起來像網路問題，其實是磁碟）。
img_ok() { local rc; qemu-img check "$1" >/dev/null 2>&1; rc=$?; [ "$rc" = 0 ] || [ "$rc" = 3 ]; }

if [ -f "$BASE" ] && ! img_ok "$BASE"; then
  echo "   ⚠ 快取 image 損毀（多半上次下載中斷），刪除重抓"
  rm -f "$BASE"
fi
# 下載到 .part、可續傳（-C -），驗過完整才 mv 成正式檔：半截檔永遠不會被當成 base。
if [ ! -f "$BASE" ]; then
  echo "   下載中（~600MB，第一次較久；斷線可重跑續傳）..."
  curl -fL -C - "$IMG_URL" -o "$BASE.part"
  img_ok "$BASE.part" || { echo "❌ 下載的 image 仍損毀，請重跑續傳"; exit 1; }
  mv "$BASE.part" "$BASE"
fi

echo "▶ 清掉舊 VM（冪等）"
virsh destroy "$VM" 2>/dev/null || true
virsh undefine "$VM" --nvram 2>/dev/null || true
rm -f "$DISK" "$CONSOLE" "$NONCE_FILE"

echo "▶ 建 overlay disk（不動原 image）"
qemu-img create -f qcow2 -F qcow2 -b "$BASE" "$DISK" 10G >/dev/null

echo "▶ 產生 cloud-init（靜態 IP + 開機自驗契約 1）"
# boot nonce：host 端也存一份，verify-range 用它判斷 console log 是不是**這顆** VM 留的。
BOOT_NONCE="$(od -An -tx1 -N16 /dev/urandom | tr -dc '0-9a-f')"
echo "$BOOT_NONCE" > "$NONCE_FILE"

UD="$(mktemp)"; NC="$(mktemp)"
# 探測腳本走 write_files + base64（與 build-golden-target.sh 同慣例）：位址與 nonce
# 當 argv 傳入，Python 內容完全不經 shell 展開，沒有跳脫地雷。
cat > "$UD" <<YAML
#cloud-config
package_update: false
write_files:
  - path: /opt/contract1_probe.py
    permissions: '0755'
    encoding: b64
    content: $(base64 -w0 "$DIR/contract1_probe.py")
runcmd:
  - [bash, -c, "python3 /opt/contract1_probe.py $MGMT_IP $RESPONSE_IP $APP_IP $BOOT_NONCE | tee /dev/console"]
YAML
cat > "$NC" <<YAML
version: 2
ethernets:
  zt:
    match: {name: "e*"}
    addresses: [$TARGET_IP/24]
    routes: [{to: default, via: $Z_TARGET_GW}]
YAML

echo "▶ 建靶機 VM 接 range-ovs / z-target（VLAN20）"
virt-install --name "$VM" --memory "$VM_MEM" --vcpus "$VM_VCPUS" \
  --disk path="$DISK",format=qcow2 --import \
  --os-variant "$OSV" \
  --network network=range-ovs,portgroup=z-target,model=virtio \
  --cloud-init user-data="$UD",network-config="$NC" \
  --serial file,path="$CONSOLE" \
  --graphics none --noautoconsole

echo "▶ 等 VM 開機 + cloud-init 跑契約 1（60–150s）..."
ok=0
for _ in $(seq 1 60); do
  if grep -q "SLICE2A-RESULT" "$CONSOLE" 2>/dev/null; then ok=1; break; fi
  sleep 3
done

echo "--- 契約 1（從真 VM 的 serial console）---"
if [ "$ok" = "1" ]; then
  grep "SLICE2A" "$CONSOLE" || true
else
  echo "❌ 沒等到 cloud-init 結果。看完整 console：$CONSOLE"
  exit 1
fi

echo "--- 契約 2（從 ns-mgmt 連 VM:$TARGET_IP，應不通）---"
ip netns exec ns-mgmt python3 "$REPO/scripts/verify_topology.py" --from-zone mgmt --target "$TARGET_IP"

if grep -q "SLICE2A-RESULT: PASS" "$CONSOLE"; then
  echo "✅ Slice 2a：真 VM 靶機接 OVS VLAN20；契約1(VM→MGMT)通、契約2(MGMT→VM)不通"
else
  echo "❌ 契約 1 未通過（見上）"
  exit 1
fi
