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
sudo bash scripts/range/build-range.sh     # OVS 六區 VLAN + router + nftables + netns policy stubs
sudo bash scripts/range/verify-range.sh    # 驗契約 1–5、APP managed paths、RED seat isolation
sudo bash scripts/range/teardown-range.sh  # 拆掉（冪等，可重複）
```

**預期**（`verify-range.sh` 末尾）：
```
契約 1：telemetry 三埠 + 指定 receiver:8000；其他 MGMT:8000/:22 不通 ... 通過
契約 2：MGMT → TARGET 反向不通 ... 拓樸契約通過（from-zone mgmt）
契約 3：RED → MGMT deny all ... 拓樸契約通過（from-zone red）
六台 red source IP：10.167.30.11 ~ .16（六個可分辨）
✅ Slice 1：四契約 + 六台 source IP 可分辨全通過（真方向性防火牆）
```

> 這一步與 GitHub Actions 的 `range.yml` 跑的是同一組腳本，CI 已綠。

---

## Step 4 — Slice 2a：靶機換成真 VM（混合模式）

一支腳本包辦：建 OVS 骨架（target 留給 VM）→ 定義 libvirt 接 OVS 的 network →
下載 cloud image → 起靶機 VM 接 VLAN20（靜態 IP `10.167.20.10`）→ 驗契約 1（從真 VM）
與契約 2（從 mgmt netns 連 VM，應不通）。

```bash
sudo bash scripts/range/build-vm-target.sh
```

**預期尾段**：
```
--- 契約 1（從真 VM 的 serial console）---
=== SLICE2A-RESULT: PASS ===
--- 契約 2（從 ns-mgmt 連 VM:10.167.20.10，應不通）---
拓樸契約通過（from-zone mgmt）。
✅ Slice 2a：真 VM 靶機接 OVS VLAN20；契約1(VM→MGMT)通、契約2(MGMT→VM)不通
```

驗證機制（免 SSH）：VM 的 serial console 導到 `/tmp/range-target-console.log`，
cloud-init 開機時跑契約 1 並把結果印到 console，host 讀該檔判定。看完整開機過程：
`sudo cat /tmp/range-target-console.log`。

VM probe 會同時驗 `MGMT_RECEIVER_IP:8000` 可達，以及一般 `MGMT_STUB_IP:8000`
不可達；兩端都有 listener，失敗不能由「服務沒啟動」假裝成防火牆封鎖。

覆寫點（環境變數）：`OSV`（os-variant，預設 `ubuntu24.04`）、`VM_MEM`、`VM_VCPUS`。
例：`sudo OSV=ubuntu22.04 bash scripts/range/build-vm-target.sh`。

> 這一步**不在 CI**：GitHub runner 無巢狀虛擬化，真 VM 只能在大主機驗。

拆除（含 VM 與 libvirt network）：`sudo bash scripts/range/teardown-range.sh`。

## Step 5 — Slice 2b-①：Falco（modern-eBPF）在真 VM 內抓到已知動作

證明 Falco 能在真 VM 內監控靶機自身 syscall 並抓到動作（#9 的真環境能力證明）。
一支腳本包辦：起 libvirt default(NAT) → 靶機 VM 開機 → cloud-init 裝 Falco
modern-eBPF、下一條自訂 rule、觸發 sentinel 檔、從 journald 撈命中 → host 讀
console 判定。

```bash
sudo bash scripts/range/build-vm-falco.sh
```

**預期尾段**：
```
=== SLICE2B1-RESULT: PASS ===
✅ Slice 2b-①：Falco（modern-eBPF）在真 VM 內監控靶機 syscall，抓到已知動作
```

> **為什麼走 NAT 不走 VLAN20**：Slice 1 的 router 刻意不做 SNAT（保六台 kali source
> IP 可分辨），VLAN20 因此無對外網路、裝不了 apt 來源的 Falco。2b-① 只證「Falco 能力
> 在此 VM kernel 成立」（你主機 BTF 已在，走 CO-RE modern eBPF 免 kernel module）。
> isolation 在 Slice 2a 已證；**Slice 4** 再把 Falco 烤成 golden image 跑在無網 VLAN20。

拆除（含這台 smoke VM）：`sudo bash scripts/range/teardown-range.sh`。

## Step 6 — Slice 2b-② / Slice 3：Falco 管線 + OTLP（compose，真環境版）

這批是 **compose 全棧**（非 VM）。CI 已驗管線本體（SQLi/OTLP/Evidence/lifecycle 全綠），
唯 **Falco 本身** CI 起不了（modern-eBPF 需真 kernel），要在大主機補：

```bash
# 起含 Falco 的全棧（--profile falco 才拉 Falco；預設 up 不含）
docker compose --profile falco up -d --build --wait --wait-timeout 180

# 跑 Falco 真環境驗收（#9）：需設 PURPLE_FALCO_ENABLED=1 才會跑那兩條
PURPLE_FALCO_ENABLED=1 PURPLE_AUTO_COMPOSE=0 \
  PURPLE_PG_DSN=postgresql://purple:purple@localhost:5432/purple \
  PURPLE_APP_URL=http://localhost:8080 PURPLE_LOKI_URL=http://localhost:3100 \
  python -m pytest -v -s -m integration tests/integration/test_falco_pipeline.py

# Slice 3-② log window / 磁碟量測（#12）：先打點流量再量
sudo bash scripts/range/measure-log-retention.sh
```

**預期**：`test_falco_exec_becomes_core_event_T1059` 綠（真 exec → Falco → Core Event
T1059）、`test_falco_disabled_rule_is_detection_gap_not_visibility_gap` 綠（telemetry_present
接真 Loki）。量測腳本印出 app/falco 行數 + Loki volume 磁碟 + retention 設定。

> Falco 起不來時看 `docker compose --profile falco logs falco`；`scap_init` 失敗＝該 kernel
> 的 modern-eBPF 環境問題（CI runner 即如此），大主機（2b-① 已證）應可。

## Step 7 — Slice 4：一鍵 range + 六台 kali + Falco golden + Reset

單主機把整組 range 起起來（組合 Slice 1 + 2a，選配 golden Falco 靶機與六台紅隊）：

```bash
# 一鍵起：靶機真 VM(VLAN20) + 四區骨架。加 --with-red 起六台 kali、--with-falco 用 golden
sudo bash scripts/range/range-up.sh --with-red --with-falco

# 驗四契約 + 六 source IP
sudo bash scripts/range/verify-range.sh

# 六台 kali 各自 source IP 打靶機 :80（示範）
for i in $(seq 1 6); do docker exec range-red$i curl -s -m3 http://10.167.20.10/ >/dev/null && echo red$i打了; done

# 演練之間 Reset（拆乾淨再重起，選項原封轉給 range-up）
sudo bash scripts/range/range-reset.sh --with-red --with-falco
```

- `--with-falco` 第一次會先 `build-golden-target.sh` 烤 golden（Falco 裝進 image，約
  3–6 分鐘，走 NAT），之後靶機在**無網 VLAN20** 開機 Falco 自動就位。
- 六台 kali 用 `RED_IMAGE` 換映像（預設 `nicolaka/netshoot`；真 kali：
  `RED_IMAGE=kalilinux/kali-rolling sudo bash scripts/range/range-up.sh --with-red`）。
- 拆除一切：`sudo bash scripts/range/teardown-range.sh`（含 red 容器與 golden build VM）。

> Slice 4 不在 CI（巢狀虛擬化）。golden image 保留在 `/var/lib/libvirt/images/`，
> Reset 不重烤；要連 golden 一起丟：`range-reset.sh --purge-golden`。

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
