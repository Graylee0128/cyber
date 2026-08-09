# 大主機 Host Setup（票 #13 / WS6）

單主機四區 range 的宿主機從零準備。**照本文順序做，可在任何一台 Ubuntu 主機重現**
建置與驗證。選型：單主機巢狀、混合 VM+容器、Open vSwitch（user 2026-08-09 拍板）。

> 已驗於：Ubuntu 24.04.4 LTS / AMD（16 核、30G RAM、`kvm_amd nested=1`、kernel BTF 存在）。
> Intel 平台把下文的 `kvm_amd` 換成 `kvm_intel` 即可，其餘相同。

---

## 需求

| 項目 | 需求 | 為什麼 |
|---|---|---|
| 架構 | x86_64 + VT-x(Intel) / AMD-V(AMD) | 硬體加速 VM |
| 巢狀虛擬化 | `kvm_intel/kvm_amd nested=1` | 大主機內開真 VM（Slice 2） |
| OS | Ubuntu 22.04 / 24.04 | 下文套件名以此為準 |
| kernel BTF | `/sys/kernel/btf/vmlinux` 存在 | Falco 走 modern eBPF，免 kernel module（Slice 2b / #9） |
| 資源（建議） | ≥ 8 核 / ≥ 16G RAM / ≥ 100G 磁碟 | 四區 + 六 kali + 觀測棧 |

---

## Step 0 — 探測能力（唯讀，先確認再裝）

```bash
sudo bash scripts/range/probe-host.sh
```

**放行條件**（全部要成立才往下）：
- CPU 虛擬化「支援」、`/dev/kvm 存在`、`nested = 1`
- `BTF 存在`（Falco modern eBPF）
- 資源達上表建議值

任一不成立：nested 未開見「疑難排解」；資源不足則縮減 kali 台數或觀測棧。

---

## Step 1 — 裝依賴（順序重要）

```bash
# 1) 套件（OVS 網路層 + libvirt/qemu 虛擬化 + virtinst 建 VM + nftables 防火牆）
sudo apt-get update
sudo apt-get install -y \
  openvswitch-switch \
  libvirt-daemon-system qemu-kvm virtinst \
  nftables cpu-checker

# 2) 服務：先起 OVS，再起 libvirt（VM 網路依賴 OVS bridge 先在）
sudo systemctl enable --now openvswitch-switch
sudo systemctl enable --now libvirtd

# 3) 群組：讓目前使用者免 sudo 操作 virsh / kvm（登出再登入才生效）
sudo usermod -aG libvirt,kvm "$USER"
```

> 依賴用途：`openvswitch-switch`＝四區 802.1Q VLAN 的軟體交換器；`libvirt-daemon-system`
> + `qemu-kvm`＝跑真 VM；`virtinst`＝`virt-install` 建 VM；`nftables`＝方向性防火牆；
> `cpu-checker`＝`kvm-ok`。

---

## Step 2 — 驗證安裝

```bash
ovs-vsctl --version | head -1     # 期望：ovs-vsctl (Open vSwitch) 3.x
virsh --version                   # 期望：10.x（24.04）
virsh net-list --all              # libvirt 就緒（此時 range-ovs 尚未定義，正常）
kvm-ok                            # 期望：KVM acceleration can be used
sudo systemctl is-active openvswitch-switch libvirtd   # 兩個都 active
```

---

## Step 3 — Slice 1：四區骨架 + 契約實測（純 netns，不需 VM）

先用 network namespace 節點把四區骨架與**方向性防火牆**跑起來，驗四條跨世代契約。
這一步不碰 VM，最快確認網路層對。

```bash
sudo bash scripts/range/build-range.sh     # OVS 四區 VLAN + router + nftables + netns 節點
sudo bash scripts/range/verify-range.sh    # 從各區驗契約 1/2/3 + 六台 source IP 可分辨
sudo bash scripts/range/teardown-range.sh  # 拆掉（冪等，可重複）
```

**預期**（`verify-range.sh` 末尾）：
```
契約 1：TARGET → MGMT ... 拓樸契約通過（from-zone target）
契約 2：MGMT → TARGET 反向不通 ... 拓樸契約通過（from-zone mgmt）
契約 3：RED → MGMT deny all ... 拓樸契約通過（from-zone red）
六台 red source IP：10.167.30.11 ~ .16（六個可分辨）
✅ Slice 1：四契約 + 六台 source IP 可分辨全通過（真方向性防火牆）
```

> 這一步與 GitHub Actions 的 `range.yml` 跑的是同一組腳本，CI 已綠。

---

## Step 4 — Slice 2：把節點換成真 VM（混合）

> 腳本開發中（Slice 2a：`build-vm-target.sh`）。定案後本節補完整步驟。順序會是：

```bash
# a) 先建 Slice 1 的 OVS 骨架（VM 要接上 br-range）
sudo bash scripts/range/build-range.sh

# b) 定義 libvirt 接 OVS 的 network（四區 portgroup 對應 VLAN）
sudo virsh net-define scripts/range/range-ovs.xml
sudo virsh net-start range-ovs
sudo virsh net-autostart range-ovs

# c) 建靶機真 VM 接 z-target（VLAN20），配靜態 IP 10.167.20.10（待 build-vm-target.sh）
# d) 從 VM 內跑 verify_topology --from-zone target 驗契約 1
```

Slice 2b：六台 kali 容器接 VLAN30 + Falco modern-eBPF 部署 target 側 +
Falco 事件走 Alloy → Loki → Core Event（#9 真環境驗收）。

---

## 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `nested = N` 或 0 | 未開巢狀虛擬化。臨時：`sudo modprobe -r kvm_amd && sudo modprobe kvm_amd nested=1`；永久：寫 `/etc/modprobe.d/kvm.conf`：`options kvm_amd nested=1`（Intel 換 `kvm_intel`），重開機 |
| `ovs-vsctl: command not found` | `openvswitch-switch` 沒裝或服務沒起：`sudo systemctl enable --now openvswitch-switch` |
| `build-range.sh` 報 OVS 連不上 | OVS daemon 沒起：`sudo systemctl status openvswitch-switch` |
| `virsh` 要 sudo 才動 | 使用者未加 `libvirt` 群組，或加了但沒重新登入 |
| `.sh` 出現 `\r` / bad interpreter | 檔案被存成 CRLF。repo 已用 `.gitattributes` 強制 `.sh` 為 LF；手動存檔請存 LF |
| VM 接不到網路（Slice 2） | `range-ovs` 未 start，或 `br-range` 尚未由 `build-range.sh` 建立 |

---

## 一頁速查

```bash
# 全新主機，從零到 Slice 1 綠：
sudo bash scripts/range/probe-host.sh                       # 0. 探測
sudo apt-get update && sudo apt-get install -y \
  openvswitch-switch libvirt-daemon-system qemu-kvm \
  virtinst nftables cpu-checker                             # 1. 裝
sudo systemctl enable --now openvswitch-switch libvirtd     # 2. 起服務
sudo bash scripts/range/build-range.sh                      # 3. 建四區
sudo bash scripts/range/verify-range.sh                     # 4. 驗契約
sudo bash scripts/range/teardown-range.sh                   # 5. 拆
```
