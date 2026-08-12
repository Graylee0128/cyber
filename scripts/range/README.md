# Range Infrastructure（票 #13 / Workstream 6）

單主機四區 range 的建置與驗收腳本。四區照 SA §12：

| 區 | VLAN | 網段 | 住戶 |
|---|---|---|---|
| Z-MGMT | 10 | 10.167.10.0/24 | 觀測／評估平面（Loki/Grafana/Prometheus/receiver/engine/PG） |
| Z-TARGET | 20 | 10.167.20.0/24 | 靶機 + collector（Alloy/Falco/response agent） |
| Z-RED | 30 | 10.167.30.0/24 | 六台 kali（各自 IP） |
| Z-APP | 40 | 10.167.40.0/24 | Range Core / Product UI（WS5） |

## 切片（單主機巢狀，選型：混合 VM+容器 + Open vSwitch）

| Slice | 內容 | 驗證環境 | 狀態 |
|---|---|---|---|
| **1** | OVS 四區 VLAN + netns 節點 + router + **nftables 真方向性防火牆** → 契約 1/2/3 + 六 source IP 可分辨 | GitHub Actions（免 nested virt） | ✅ CI 綠 |
| **2a** | 靶機 netns → 真 VM（KVM/libvirt）接 VLAN20 → 契約 1/2 | 大主機 | ✅ 大主機綠 |
| **2b-①** | Falco（modern-eBPF）在真 VM 內抓到 syscall | 大主機 | ✅ 大主機綠 |
| **2b-②** | Falco→Alloy→Loki→Grafana→Core Event(T1059) + 決定性測試 | compose（CI 綠除 Falco 本身）/ 大主機 `--profile falco` | ✅ 管線 CI 綠 |
| **3** | OTLP `:4317` push 取代 scrape（#11）；log window/磁碟量測（#12） | compose CI（OTLP）/ 大主機（量測） | ✅ OTLP CI 綠 |
| **4** | 六台 kali 接 VLAN30、Falco golden image 跑無網 VLAN20、Reset、一鍵 IaC | 大主機 | ✅ 大主機綠（2026-08-10，四層測試全通過）|

**為什麼 Slice 1 走 CI**：OVS + network namespace + nftables 不需巢狀虛擬化，
GitHub Actions 標準 runner 就能跑，所以四區骨架（真 VLAN tag + 真方向性防火牆 +
source IP 可分辨）能先拿到 CI 綠燈。真 VM（Slice 2）才需要你大主機的 nested KVM。

## 四條跨世代契約（SA §12.2）與這裡怎麼真做

| # | 契約 | Slice 1 怎麼強制 |
|---|---|---|
| 1 | TARGET→MGMT telemetry `:3100/:9090/:4317` 通；只對指定 receiver 放行 `:8000`；其他 MGMT `:8000/:22` 不通 | nftables 三埠 allowlist + `MGMT_RECEIVER_IP:8000` 精準例外；兩個 MGMT netns 都起真 listener 驗最小權限 |
| 2 | MGMT→TARGET 反向**不通**（單向） | nftables `policy drop` + 只放 established 回程，無 mgmt→target new 規則 |
| 3 | RED→MGMT **deny all** | nftables 無 VLAN30→VLAN10 規則，被 policy drop |
| 4 | collector 在 target 側 | 節點歸屬（Slice 2 接真 collector） |
| ＋ | 六台 red 六個可分辨 source IP | router **不做 SNAT** → source IP 保留（防 G0 塌縮） |

`#15` 的 docker network membership 只能做「可達/不可達」，做不到**方向**；這裡用
nftables 在 router 的 forward hook 做真方向性，正是 #15 明白委派出來的契約 2。

## 用法（需 root）

> **全新主機從零到 Slice 1 綠**：照 [HOST-SETUP.md](./HOST-SETUP.md) 的安裝順序做
> （探測 → 裝依賴 → 驗證 → 建四區 → 驗契約），可在任何 Ubuntu 主機重現。

```bash
sudo bash scripts/range/build-range.sh     # 建四區
sudo bash scripts/range/verify-range.sh    # 驗四契約 + source IP
sudo bash scripts/range/teardown-range.sh  # 拆掉（冪等，Reset 雛形）
```

依賴：`openvswitch-switch`、`nftables`、`iproute2`、`python3`。Ubuntu：
`sudo apt-get install -y openvswitch-switch nftables`。

## 元件

- `HOST-SETUP.md` — 從零安裝順序（探測→依賴→驗證→建置→驗收），可在其他主機重現
- `probe-host.sh` — 大主機能力探測（nested KVM / libvirt / OVS / Falco BTF / 資源）
- `build-range.sh` — OVS bridge + 四 VLAN + router（inter-VLAN + nftables）+ 節點 netns
- `verify-range.sh` — 從各區 netns 跑 `verify_topology.py` 驗契約 + source IP 可分辨
- `build-vm-target.sh` — Slice 2a：靶機真 VM（cloud image）接 OVS VLAN20，驗契約 1/2
- `build-vm-falco.sh` — Slice 2b-①：真 VM 內裝 Falco（modern-eBPF），驗抓到已知動作
- `build-golden-target.sh` — Slice 4：產出 Falco 已烤進的 golden 靶機 image
- `attach-red.sh` — Slice 4：六台紅隊容器接 VLAN30（各自 source IP）
- `range-up.sh` — Slice 4：一鍵起整組 range（IaC；`--with-red` / `--with-falco`）
- `range-reset.sh` — Slice 4：Reset（拆乾淨再重起）
- `measure-log-retention.sh` — Slice 3-②：Loki 行數 + 磁碟用量 + retention 量測（#12）
- `lib-cloudimg.sh` — 共用：cloud image 下載 + `qemu-img check` 完整性把關（防半截 image）
- `range-ovs.xml` — libvirt 接 OVS（br-range）的 network 定義，四區 VLAN portgroup
- `teardown-range.sh` — 拆除 netns / VM / red 容器 / golden（可重複跑，Reset 基礎）
- `stub_listener.py` — mgmt/receiver 佔正向與 deny-canary port；target 聽 :80 記錄 red source IP

契約判定邏輯在 `src/purple/topology_check.py`（已單元測試），CLI 在
`scripts/verify_topology.py`。
