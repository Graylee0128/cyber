#!/usr/bin/env bash
# 票 #13 Slice 4 / #9 —— 產出 golden 靶機 image：Falco ＋ Alloy ＋ 靶機 app 全烤進去。
#
# 為什麼要 golden：VLAN20 刻意無對外網（保六台紅隊 source IP 可分辨），靶機在 VLAN20
# 上裝不了 apt 套件。所以先在**有網的 NAT** 下把東西裝好、enable service，關機，把
# disk 轉成 golden；之後 range-up --with-falco 用它當 base 跑在無網 VLAN20，開機即就位：
#
#   靶機 app(:80) 被紅隊打 → Falco(modern-eBPF) 抓 syscall → Alloy 推 Z-MGMT 的 Loki
#   （TARGET→MGMT :3100＝契約 1 實用）→ Grafana → webhook → Core Event
#
# 烤進去的內容都是 repo 裡的**真檔案**（base64 注入 cloud-init，不在腳本裡埋碼）：
#   deploy/range-target/{app.py,config.alloy,bake.sh}
#   deploy/falco/rules.d/purplescope.yaml   ← 與 compose 的 falco 共用同一份，不會漂移
#
# 需 root + Slice 2 依賴。**不在 CI**（無巢狀虛擬化）。覆寫點：OSV、VM_MEM、VM_VCPUS。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DIR/../.." && pwd)"
# shellcheck source=scripts/range/lib-cloudimg.sh
source "$DIR/lib-cloudimg.sh"
# shellcheck source=scripts/range/zones.env
source "$DIR/zones.env"

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

echo "▶ 確保 libvirt default（NAT）網路已起（烤東西要 internet）"
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

echo "▶ 產生 cloud-init（base64 注入 repo 真檔案，免縮排/跳脫地雷）"
b64() { base64 -w0 "$1"; }
UD="$(mktemp)"
cat > "$UD" <<YAML
#cloud-config
package_update: false
write_files:
  - path: /etc/falco/rules.d/purplescope.yaml
    encoding: b64
    content: $(b64 "$REPO/deploy/falco/rules.d/purplescope.yaml")
  - path: /etc/alloy/config.alloy
    encoding: b64
    content: $(b64 "$REPO/deploy/range-target/config.alloy")
  - path: /opt/range-target/app.py
    encoding: b64
    content: $(b64 "$REPO/deploy/range-target/app.py")
  - path: /opt/bake.sh
    permissions: '0755'
    encoding: b64
    content: $(b64 "$REPO/deploy/range-target/bake.sh")
  - path: /etc/purplescope/secret.txt
    permissions: '0600'
    content: |
      PURPLESCOPE-RANGE-SECRET
      這是 SA §7 Scenario 03（敏感檔存取）的標的檔。刻意用專屬路徑而非真的系統憑證檔，
      讓演練可重現、可清除，且不需要碰 /etc/shadow。
runcmd:
  - [bash, /opt/bake.sh]
YAML

echo "▶ 起 build VM（NAT），烤完自動關機（裝 Falco+Alloy，約 4–8 分鐘）"
virt-install --name "$VM" --memory "$VM_MEM" --vcpus "$VM_VCPUS" \
  --disk path="$BUILD",format=qcow2 --import \
  --os-variant "$OSV" \
  --network network=default,model=virtio \
  --cloud-init user-data="$UD" \
  --serial file,path="$CONSOLE" \
  --graphics none --noautoconsole

echo "▶ 等烤完 + VM 關機..."
ok=0
for _ in $(seq 1 200); do   # 200×3s = 600s
  state="$(virsh domstate "$VM" 2>/dev/null || echo unknown)"
  # DONE 與 ABORT 都算「跑完了」——ABORT 的情形下 bake.sh 自己會 poweroff -f，
  # 不提早結束的話這裡會白等滿 600s 才報一個沒資訊的逾時。
  if grep -qE "GOLDEN-BAKE-(DONE|ABORT)" "$CONSOLE" 2>/dev/null && [ "$state" = "shut off" ]; then
    ok=1; break
  fi
  sleep 3
done

if [ "$ok" != 1 ]; then
  echo "❌ 沒等到烤完+關機。看 console：sudo cat $CONSOLE"
  exit 1
fi
grep -E "GOLDEN-(BAKE|FALCO|ALLOY|APP|RULE)" "$CONSOLE" || true

# bake 期自證：三個 service active、兩條 rule 各命中至少一次。任一不成立就別產 golden ——
# 產出壞 golden 只會把問題推遲到上線後更難查。
bake_fail=0
grep -q "GOLDEN-FALCO-STATE: active" "$CONSOLE" || { echo "❌ falco 未 active"; bake_fail=1; }
grep -q "GOLDEN-ALLOY-STATE: active" "$CONSOLE" || { echo "❌ alloy 未 active"; bake_fail=1; }
grep -q "GOLDEN-APP-STATE: active"   "$CONSOLE" || { echo "❌ 靶機 app 未 active"; bake_fail=1; }
if grep -qE "GOLDEN-RULE-HITS: exec=[1-9][0-9]* secret=[1-9][0-9]* uncovered=[1-9][0-9]*" "$CONSOLE"; then
  echo "   ✅ Falco 三條 rule（T1059 exec / T1005 敏感檔 / uncovered）在 VM 內實際命中"
else
  echo "❌ Falco rule 未全部命中（看 GOLDEN-RULE-HITS）"; bake_fail=1
fi
# uncovered 這條的意義是「有遙測、但刻意沒有 Grafana 規則覆蓋」——決定性測試（ADR ③）
# 的真環境素材。它在 bake 期就要證明 Falco 抓得到，否則上線後那條測試會分不出
# 「規則沒覆蓋」與「Falco 根本沒看到」。
if grep -q "GOLDEN-BAKE-ABORT" "$CONSOLE" 2>/dev/null; then
  echo "❌ bake 中途失敗（GOLDEN-BAKE-ABORT）"; bake_fail=1
fi
if [ "$bake_fail" != 0 ]; then
  echo
  echo "──────── VM 內診斷（DIAG 標記；本 VM 無 SSH，console 是唯一窗口）────────"
  # 用標記精準撈 —— 關機序列有上百行，tail 會把診斷淹掉。
  grep -a "DIAG|" "$CONSOLE" | sed 's/.*DIAG| //' | tail -70 || echo "(沒有 DIAG 輸出)"
  echo "──────────────────────────────────────────────────────────────────────"
  echo "❌ bake 自證未過，不產 golden。完整記錄：sudo cat $CONSOLE"
  exit 1
fi

echo "▶ 把烤好的 disk 轉成獨立 golden image（$GOLDEN）"
qemu-img convert -O qcow2 "$BUILD" "$GOLDEN"
# 蓋上內容指紋：來源檔一改，range-up 就知道這顆 golden 過時、自動重烤。
golden_stamp "$REPO" > "$GOLDEN.stamp"
virsh undefine "$VM" --nvram 2>/dev/null || true
rm -f "$BUILD"

echo "✅ Golden image 就緒：$GOLDEN"
echo "   內含：Falco(modern-eBPF) + Alloy(推 $MGMT_LOKI_IP:3100) + 靶機 app(:80)，全部 enable"
echo "   用法：range-up.sh --with-falco"
