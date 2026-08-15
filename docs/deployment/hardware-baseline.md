# WS6 Deployment Baseline — Single-Node Hardware Requirement & Validation

> 對應 [#137](https://github.com/Graylee0128/cyber/issues/137)。本文件是部署資格認證（deployment qualification），不是承載上限測試——上限已由 [#78](https://github.com/Graylee0128/cyber/issues/78) 驗證。

## 1. Purpose

回答三個問題：

1. 最低什麼硬體規格可以**完整部署** Cyber？
2. 在典型 50–70 人演練負載下，這個規格是否有合理 headroom？
3. `README` / 部署文件最終該寫的 Minimum / Recommended Hardware 是什麼？

不找 single host 最大承載量——已由 #78 驗證完畢（6C/10G VM，70 containers 約 3.7GiB，推到 230 才碰 RAM bottleneck）。

## 2. Scope

**Included**：Single-node 部署、標準 Cyber 演練（production-equivalent stack）、Target VM、Red/Blue seats（依現行 seat model 換算）、Observability/Detection（Alloy → Loki → Grafana，Falco telemetry）。

**Excluded**：DDoS/packet flood、Full PCAP、Malware sandbox、Multi-host 部署、超過已驗證人數的 participant envelope。

## 3. Reference Evidence

[#78](https://github.com/Graylee0128/cyber/issues/78) 既有實測（直接引用，不重測）：Host 6C/10GiB/97GB、kernel 6.8、VirtualBox VM；70 containers（完整 stack + target VM）約 3.7GiB RAM；推到 230 containers 才碰 RAM bottleneck；startup lower bound 約 0.5 秒/container（`attach-red.sh` 的 `sleep infinity`，非完整 seat provisioner 開銷）。

**#78 是 capacity evidence，不是 production minimum**——VirtualBox 巢狀虛擬化與尚非完整 production seat workload 是兩個已知落差。本文件的方法論刻意避開同樣的落差（見 §4 巢狀虛擬化層級說明）。

## 4. Test Environment

| 項目 | 值 |
|---|---|
| Host | `100.70.108.88`（Tailscale，共用機器，非專用） |
| CPU | AMD Ryzen 7 9700X（8C/16T，實際可用 16 vCPU） |
| RAM | 32 GiB（實測 `free -h` 30Gi） |
| Disk | NVMe，1.7TB 可用 |
| Kernel | 6.0.0-28-generic（Ubuntu 24.04.4 LTS） |
| KVM | `/dev/kvm` 存在，nested=1（未使用，見下） |
| Docker | Cgroup Driver: systemd, Cgroup Version: 2 |
| Cyber commit SHA | `78db5cc47a000f05d4aa01ef60ec602eedf398e3`（master） |
| 日期 | 2026-08-15 |

### 硬體 profile 模擬方法（重要）

**不使用巢狀 VM 模擬 profile**——若採「實體機 → profile VM → target VM」雙層巢狀，會把「巢狀虛擬化本身的效能損耗」混進「這規格夠不夠格」的答案裡，失真，且與正式單機部署的巢狀層級（只有一層：實體機 → target VM）不一致。

改用**單層 cgroup v2 資源圈禁**，在裸機上直接模擬較小規格：

1. 建一個 systemd slice（如 `cyber-low.slice`），設定 `CPUQuota` / `MemoryMax` / `MemorySwapMax=0` 對應該 profile 的 vCPU/RAM 上限。
2. Docker compose 每個 service 加 `cgroup_parent: <slice>`，讓容器算進同一份配額（僅本機臨時修改，未進 git）。
3. **Target VM 用 libvirt domain XML 的 `<resource><partition>` 指到同一個 slice**（如 `/cyber/low`，對應 systemd 的 slice 命名慣例：`/`分隔的每一段各自轉成 `-` 串接的 slice 名稱，**不要**在路徑裡自己加 `.slice` 字尾，否則會被systemd-machined 二次跳脫成完全不同、沒有配額的孤兒 slice——這是本輪實測踩到的真坑，見下方「方法論踩坑記錄」）。這讓 VM 的 qemu process **從一開始建立就活在配額內**，而不是開機後才搬進去。
4. **cgroup v2 對記憶體充公頁面（charged page）不會在 process 搬家時追溯改記帳**——若 VM 先在配額外開機、之後才把 PID 搬進 slice，只有搬家後才 fault 的頁面會被算到新 slice，已經 touch 過的記憶體（可能佔真實用量的大部分）仍算在舊 cgroup，導致嚴重低估。**必須讓 VM 從程序誕生的那一刻起就已經在目標 slice 裡**（透過 domain XML 的 partition，而非事後 `cgroup.procs` 搬移）。

#### 方法論踩坑記錄（供下一輪 Candidate Minimum / Recommended 兩個 profile 沿用）

- 事後搬移 PID 進 slice：VM 顯示只佔 6.5MB，但 `/proc/PID/status` 的 `VmRSS` 實測 1.06GB——確認是充公頁面未追溯記帳，數字不能用。
- 用 `<partition>/cyber.slice/cyber-low.slice</partition>`（路徑段自己帶 `.slice`）：systemd-machined 把每一段再各自跳脫加 `.slice`，變成 `cyber.slice.slice` / `cyber.slice-cyber\x2dlow.slice.slice`——一個全新、沒有任何配額的孤兒 slice，VM 完全不受限，且會在 host 上留下孤兒 systemd 單元（`systemctl list-units 'cyber*'` 看得到，已於本輪清除）。
- 正確寫法：`<partition>/cyber/low</partition>`——`/`分隔的兩段 `cyber`、`low`，systemd 依慣例組出 `cyber.slice`（父）與 `cyber-low.slice`（子），**剛好對上**手動建立的 `/etc/systemd/system/cyber-low.slice`。`virsh define` 覆寫既有 domain 定義後，`virsh destroy && virsh start` 重開機，`/proc/PID/cgroup` 才確認落在正確路徑。

### Falco 模式

`deploy.sh` 自動選 `FALCO_MODE=vm`（host kernel 6.0 高於 HOST-SETUP.md 的 ≤6.8 前提，容器模式不可用）——Falco/Alloy/app 已烤進 target golden VM，**不是**另外一台獨立的 Falco VM。本輪實測只有一台 target VM（2048 MiB / 2 vCPU，`build-vm-target.sh` 寫死值，不隨 profile 調整——這是平台架構的固定成本，非彈性配額）。

AI 輔助（#131）在本次測試環境**不存在**——`.88` 的 master 尚未 merge PR #136，deploy.sh 沒有 Ollama 相關段落，本輪報告不涵蓋 AI 輔助的資源占用。

## 5. Results

### 5.1 Low profile（4C / 8G）—— 已完成 S0 / S1

| State | 內容 | 結果 |
|---|---|---|
| **S0 Idle** | slice 建立，OS 開機，Cyber 未部署 | `memory.current=0`；`memory.max=8589934592`（8GiB 準確）；`cpu.max=400000 100000`（400% CPUQuota 準確） |
| **S1 Platform** | 完整 L1 compose（8 個預設 service）+ L2 range（target VM + 6 台紅隊容器） | 見下表 |

**S1 Platform 詳細數據**（VM 從第二次重開機、partition 修正後才落在配額內量測，boot 完成後穩態值）：

| 指標 | 數值 | 佔配額 |
|---|---|---|
| RAM 總用量（8 容器 + target VM） | 1,518,325,760 bytes ≈ 1.41 GiB | 17.7%／8 GiB |
| ——其中 target VM 單獨 | 1,096,077,312 bytes ≈ 1.02 GiB | 51%／VM 自身 2GiB 配額 |
| ——其中 8 個 docker 容器合計 | ≈ 402 MiB（總量減 VM） | — |
| Swap 使用 | 0 bytes | 不依賴 swap ✓ |
| CPU（累積 usage_usec，約 6 分鐘運行） | 84.16s CPU-time | 平均 ≈23%／4 vCPU，無 throttling（`nr_throttled=0`） |
| Memory pressure 事件 | `low=0 high=0 max=0 oom=0 oom_kill=0` | 無壓力 ✓ |
| Disk footprint（部署前後差） | 52,547,744 → 52,595,832 KB，Δ≈47 MB | 輕量（golden image 已快取） |
| Health checks | 8/8 service `Up`，6 個定義 healthcheck 的全 `healthy`（alloy/loki 未定義 healthcheck，非缺陷） | 全通 ✓ |
| Target VM 功能性 smoke | `curl` 打 `/` 回 `404`（服務有回應，路由本身如此，非故障） | 通 ✓ |
| Startup | cloud-init 契約 1 自驗完成，穩態落在 boot 後 ~90–150s 區間內 | 符合 script 註解預期 |

**S1 Platform 在 Low(4C/8G) profile 下結論**：輕鬆通過，RAM 用量遠低於配額（82% headroom），無 swap、無 OOM、無 CPU throttling。這代表 **Platform 開銷本身很小**——4C/8G 對「只跑平台、沒有演練負載」綽綽有餘。真正會逼近 Low profile 上限的是 S2（見下）。

### 5.2 Candidate Minimum（6C/12G）、Recommended（8C/16G）

**尚未執行**——本輪先以 Low profile 驗證方法論（cgroup slice + VM partition 的雙重圈禁）走得通，確認可重複後再依序跑另外兩個 profile 的 S0/S1。

### 5.3 S2 Typical Exercise（三個 profile 皆未執行）——**blocked**

`scripts/range/seat_provisioner.py`（[#62](https://github.com/Graylee0128/cyber/issues/62)）雖然 issue 已 closed、腳本存在，但**沒有被接進 `docker-compose.yml`**——沒有對應 service，不會隨 `docker compose up` 自動啟動，「座位卡 `requested` 出不去」（與先前 `.88` 完整頁面測試抓到的缺口一致）。

本票 Authoritative blockers 明文要求 S2「不能用近似值頂替，否則 Recommendation 章節的數字會繼承 #78 已知的『非完整 production seat workload』落差」，因此**不採用手動模擬 seat 的替代方案**。等 Gray 那邊把 seat provisioner 接線修好後再補測。

## 6. Recommendation

**待補**——S2 未完成前不拍板 Minimum/Recommended 正式規格。目前唯一可確定的：S1（平台本身，不含演練負載）在 4C/8G 已有大量餘裕，代表最終瓶頸會落在 seat 規模化，而非平台常駐開銷，與 #78「第一個瓶頸是 RAM，非 CPU」的結論方向一致。

## 7. Validated Envelope

**已驗證**：1 host（裸機，非巢狀 VM）+ 1 target VM（Falco/Alloy/app 烤進 golden image）+ 6 台固定紅隊容器 + 完整 L1 觀測棧，在 Low(4C/8G) cgroup 圈禁下的 Platform 狀態（S1）。

**未驗證**：volumetric DDoS、heavy PCAP、malware detonation、S2 典型演練負載（動態 Red/Blue seat）、Candidate Minimum(6C/12G)／Recommended(8C/16G) 兩個 profile、multi-host 部署。

## 8. Revalidation Triggers

需要重跑本 baseline 的情況：participant envelope 提高、新增重量級 service、加入 IDS/PCAP、加入 DDoS scenario、target VM 數量增加、container→VM 架構改變、single-node→multi-host、telemetry volume 模型重大改變、seat provisioner（#62）接線方式改變（會直接影響 S2 的量測方法）。

---

## 執行狀態（供接手者參考，非正式章節）

- [x] 方法論設計並驗證可行（cgroup slice + VM domain partition 雙重圈禁，單層虛擬化，不失真）
- [x] Low(4C/8G) profile：S0 + S1
- [ ] Low(4C/8G) profile：S2（blocked，等 #62 seat provisioner 接線）
- [ ] Candidate Minimum(6C/12G)：S0 + S1（方法已驗證，可直接套用）
- [ ] Recommended(8C/16G)：S0 + S1
- [ ] 第 6 節 Recommendation 待全部 S2 數據到齊才能拍板
- [x] `.88` 已還原乾淨（git checkout 復原 `docker-compose.yml`、slice 單元已移除、容器/VM/network 全部 teardown）
