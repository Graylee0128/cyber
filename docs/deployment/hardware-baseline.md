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
| Cyber commit SHA | Low profile：`78db5cc`（master）；Candidate/Recommended：`9508ab6`（`issue-137-hardware-baseline`，含 `#131` PR #136 merge 後的 master + 本票自己的 `#62` seat provisioner 接線修復） |
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

AI 輔助（#131）：Low profile 那輪測試環境**不存在**（`.88` 的 master 當時尚未 merge PR #136）。Candidate/Recommended 兩輪已切到含 PR #136 的 commit，`deploy.sh` 會自動拉 Ollama + `qwen2.5:3b`（磁碟空間夠，AI_ENABLED=1），因此這兩輪的 S1 數據**包含** Ollama container。

**已知量測限制**：`attach-red.sh` 建的 6 台紅隊容器是 `docker run` 直接起（不是 compose service），不吃得到 compose 的 `cgroup_parent` 設定，因此**不計入**下面各 profile 的 slice 總量——三個 profile 對這點的處理方式一致（都漏），彼此可比較，但每個 profile 的絕對數字都會比實際略低一點（缺 6 台輕量容器的份量，估計數十 MB 等級，不影響結論）。

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

### 5.2 Candidate Minimum（6C/12G）—— 已完成 S0 / S1

| State | 內容 | 結果 |
|---|---|---|
| **S0 Idle** | slice 建立，Cyber 未部署 | `memory.current=0`；`memory.max=12884901888`（12GiB 準確）；`cpu.max=600000 100000`（600% 準確） |
| **S1 Platform** | 完整 L1 compose（9 個 service，含 Ollama）+ L2 range（target VM + 6 台紅隊容器） | 見下表 |

| 指標 | 數值 | 佔配額 |
|---|---|---|
| RAM 總用量 | ≈3362 MiB ≈ 3.28 GiB | 27.4%／12 GiB |
| ——其中 target VM 單獨 | ≈1066 MiB | 51%／VM 自身 2GiB 配額，與 Low profile 一致 |
| ——其中 9 個 docker 容器合計（含 Ollama） | ≈2293 MiB | 見下方 Ollama 註記 |
| Swap 使用 | 0 bytes | 不依賴 swap ✓ |
| CPU（累積 usage_usec，約 9 分鐘運行） | 68.72s CPU-time | 平均 ≈13%／6 vCPU，無 throttling |
| Memory pressure 事件 | 全 0 | 無壓力 ✓ |
| Disk footprint | 52,595,832 → 58,041,488 KB，Δ≈5.3 GB | 主要是 Ollama image + `qwen2.5:3b` 模型檔（首次拉取） |
| Health checks | 9/9 `Up`，定義 healthcheck 的全 `healthy` | 全通 ✓ |
| Target VM 功能性 smoke | `404`（有回應） | 通 ✓ |

**Ollama 記憶體量測波動（重要方法論註記）**：這輪 deploy.sh 執行過程中跑了 `ollama pull qwen2.5:3b`，容器記憶體用量（≈2.29GiB）明顯高於 Recommended profile那輪同樣有 Ollama 但未觸發模型載入的量測（≈0.4GiB，見 5.3）。研判是 Ollama 在 pull／健康檢查過程中曾把模型權重載進記憶體做驗證，之後未必會自動釋放。**這代表 Ollama 的實際 RAM 佔用高度依賴「模型是否曾被載入推論」，idle server-only 與「模型常駐記憶體」兩種狀態差距可達 ~2GiB**——這點對 `#131` 自己的資源估算也有參考價值，但不在本票修正範圍內，僅此記錄。

### 5.3 Recommended（8C/16G）—— 已完成 S0 / S1

| State | 內容 | 結果 |
|---|---|---|
| **S0 Idle** | slice 建立，Cyber 未部署 | `memory.current=0`；`memory.max=17179869184`（16GiB 準確）；`cpu.max=800000 100000`（800% 準確） |
| **S1 Platform** | 完整 L1 compose（9 個 service，含 Ollama）+ L2 range（target VM + 6 台紅隊容器） | 見下表 |

| 指標 | 數值 | 佔配額 |
|---|---|---|
| RAM 總用量 | ≈1459 MiB ≈ 1.42 GiB | 8.9%／16 GiB |
| ——其中 target VM 單獨 | ≈1062 MiB | 與另外兩個 profile 一致 |
| ——其中 9 個 docker 容器合計（含 Ollama，**未載入模型**） | ≈397 MiB | 見上方 Ollama 註記——這輪 Ollama 是 idle 狀態 |
| Swap 使用 | 0 bytes | 不依賴 swap ✓ |
| CPU（累積 usage_usec，約 4 分鐘運行） | 29.21s CPU-time | 平均 ≈12%／8 vCPU，無 throttling |
| Memory pressure 事件 | 全 0 | 無壓力 ✓ |
| Disk footprint | 58,041,488 → 58,092,984 KB，Δ≈52 MB（image 已快取，僅 VM overlay/flag） | 輕量 |
| Health checks | 9/9 `Up`，定義 healthcheck 的全 `healthy` | 全通 ✓ |
| Target VM 功能性 smoke | `404`（有回應） | 通 ✓ |

### 三個 profile 的 S1 Platform 小結

三個 profile 在 Platform 狀態（不含演練負載）下**全部輕鬆通過**，headroom 都在 70%+ 以上（Low 82%、Candidate 73%、Recommended 91%）。CPU 三輪皆無 throttling，記憶體三輪皆無 pressure/OOM/swap。**代表平台常駐開銷本身很小，連 Low(4C/8G) 都綽綽有餘**——三個 profile 在 S1 這一層事實上難以區分優劣，真正會拉開差距、決定 Candidate Minimum 能不能定案的是 S2（見下）。

### 5.4 S2 Typical Exercise（三個 profile 皆未執行）

`scripts/range/seat_provisioner.py`（[#62](https://github.com/Graylee0128/cyber/issues/62)）原本雖然程式碼與測試都已交付，但**沒有被任何腳本啟動**——導致座位卡在 `requested` 出不去。**本票這輪已經修好這個接線**（`scripts/range/seat-provisioner-daemon.sh`，見同一個 PR 的另一顆 commit），並在 `.88` 上端到端驗證過：真實建立一顆 pending seat、provisioner 3 秒內撿到、建出真容器、接上 VLAN30 拿到真 IP。

但 S2 本身**這輪仍未執行**——用真的 30 Red + 20 Blue seat 跑一輪需要先讓一場 exercise 走完整的「建立 → prepare → 領位」流程，而 [#143](https://github.com/Graylee0128/cyber/issues/143) 項目 1 指出這條路徑目前**沒有合法呼叫者**（`prepare` 端點只認 `admission` 服務身分，但沒有任何呼叫端會用這個身分打它），也就是說即使 provisioner 已經能動，S2 要做的「開一場正式演練、動態灌入典型人數」這件事本身還卡著另一個尚待解的缺口。留給下一輪或等 `#143` 項目 1 解決後再測。

## 6. Recommendation

**待補**——S2 未完成前不拍板 Minimum/Recommended 正式規格。目前可確定的：S1（平台本身，不含演練負載）在三個 profile 下都有大量餘裕（Low 82%、Candidate 73%、Recommended 91%），代表最終瓶頸會落在 seat 規模化，而非平台常駐開銷，與 #78「第一個瓶頸是 RAM，非 CPU」的結論方向一致。**傾向**：Low(4C/8G) 作為 Candidate Minimum 的下限候選看起來過於寬鬆（S1 才用 18%），真正的下限判定必須等 S2 數據，不能用 S1 的餘裕反推。

## 7. Validated Envelope

**已驗證**：1 host（裸機，非巢狀 VM）+ 1 target VM（Falco/Alloy/app 烤進 golden image）+ 6 台固定紅隊容器 + 完整 L1 觀測棧（含 Ollama），在 Low(4C/8G)／Candidate Minimum(6C/12G)／Recommended(8C/16G) 三個 cgroup 圈禁 profile 下的 Platform 狀態（S1）。`#62` seat provisioner 接線本身端到端驗證過（見 5.4），但未在完整 S2 演練負載下測過。

**未驗證**：volumetric DDoS、heavy PCAP、malware detonation、S2 典型演練負載（三個 profile 皆未執行，卡在 #143 項目 1 的 exercise 建立流程缺口）、multi-host 部署。

## 8. Revalidation Triggers

需要重跑本 baseline 的情況：participant envelope 提高、新增重量級 service、加入 IDS/PCAP、加入 DDoS scenario、target VM 數量增加、container→VM 架構改變、single-node→multi-host、telemetry volume 模型重大改變、Ollama 模型換版或常駐推論策略改變（見 5.2 的記憶體波動註記）。

---

## 執行狀態（供接手者參考，非正式章節）

- [x] 方法論設計並驗證可行（cgroup slice + VM domain partition 雙重圈禁，單層虛擬化，不失真）
- [x] Low(4C/8G) profile：S0 + S1
- [x] Candidate Minimum(6C/12G) profile：S0 + S1
- [x] Recommended(8C/16G) profile：S0 + S1
- [x] `#62` Seat Provisioner 接線修復並端到端驗證（另一顆 commit，同一個 PR）
- [ ] 三個 profile 的 S2（現在 provisioner 能動了，但卡在 #143 項目 1：exercise 建立流程沒有合法呼叫者，見 5.4）
- [ ] 第 6 節 Recommendation 待全部 S2 數據到齊才能拍板
- [x] `.88` 每輪測試後都已還原乾淨（git checkout 復原 `docker-compose.yml`、slice 單元已移除、容器/VM/network 全部 teardown）
