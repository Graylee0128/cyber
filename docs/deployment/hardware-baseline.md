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

### 5.4 S2 Typical Exercise —— Candidate Minimum(6C/12G) 已完成

**這條路線走通了**。`#62` seat provisioner 接線修好（`seat-provisioner-daemon.sh`）之後，[#143](https://github.com/Graylee0128/cyber/issues/143) 項目 1（prepare 端點沒有合法呼叫者）也在同一輪修好（見另一個 PR #145），兩者一起解除了 S2 的執行阻礙。過程中額外抓到 **3 個真的 production bug**，全部修進本 PR 才讓 S2 真的跑得起來：

1. **`purplescope/blue-seat:latest` 從來沒有任何腳本會 build**（紅隊靠 `attach-red.sh` 順便建，藍隊完全沒有對應步驟）——加 `_ensure_image_built()`。
2. **`deploy/blue-seat/Dockerfile` 的 `COPY` 路徑假設錯的 build context**（寫死 repo-root 相對路徑，但唯一真的呼叫路徑用的是 image 自己的資料夾）——這代表藍隊座位這條路徑在這次修復前**從沒被成功 build 過一次**，不是理論上的邊界情況。
3. **`_attach_container_to_vlan` 的 veth peer 名 `cs0` 是寫死字面值**，跨所有 seat 共用；只要有一次建置失敗在 peer 端搬進 netns 之前，就會孤兒卡住、擋住**之後所有**座位（不分紅藍）的建置。這次量測撞到 3 個座位卡死超過 150 秒，log 累積 737 筆 ERROR，才追出來。

**執行方式**：真實 HTTP 流程（不是模擬）——`POST /admission/prepare` 拿到 Range Core 生成的 `exercise_id` → `PUT .../pool-config`（`red_cap=30, blue_cap=20`）→ **藍隊先 claim（20 次，鎖之前）** → 紅隊 claim（30 次）→ provisioner 輪詢建置。

**踩到的第 4 個坑（流程性，非 bug）**：`pool-config/lock` 的語意跟直覺相反——**上鎖後藍隊自助 claim 會被擋**（`request_blue_seat` 檢查 `locked_at IS NOT NULL` 就直接拒絕）。正確順序是先讓藍隊 claim 完，鎖只是用來凍結名冊、不是開放 claim 的信號。第一次測試順序搞反，卡出一個沒有任何取消機制的 `prepared` 殭屍 exercise（呼應 `#143` 討論時就發現的「沒有取消預備」缺口），只能直接動 DB 清掉重來。

**S2 結果**（Candidate Minimum 6C/12G，30 Red + 20 Blue，共 70 個動態 seat 容器 + 6 台固定紅隊容器 + target VM + 17 個 compose service）：

| 指標 | 數值 | 備註 |
|---|---|---|
| 動態 seat 容器建置成功率 | 70/70（100%） | 30 紅（各 1 台）+ 20 藍（各 2 台 a/b） |
| RAM（compose 17 服務 + 70 seat 容器，cgroup 準確量測） | ≈705 MiB | 目標 VM 這輪未重新綁定 cgroup partition（見下方限制），另計 |
| RAM（target VM，`/proc/PID/status` 直讀） | ≈1.13 GiB | 與其他 profile 一致（golden image 開機後的穩態值） |
| **RAM 合計（可信估計）** | **≈1.83 GiB** | 705 MiB + 1.13 GiB；6 台固定紅隊容器（`attach-red.sh`）不在任何 cgroup 量測範圍內，估計數十 MB 等級 |
| 佔 Candidate Minimum 配額 | **≈15%／12 GiB** | 大量餘裕 |
| Swap / OOM / CPU throttling | 全 0 | ✓ |
| CPU（累積 usage_usec，約 15 分鐘含建置過程） | 196.13s CPU-time | 平均 ≈22%／6 vCPU |
| Disk footprint | Δ≈427 MB（70 個輕量容器的可寫層，image 已快取） | 輕量 |
| Health checks | 17/17 compose service `Up`，全 `healthy` | ✓ |
| 功能性 smoke | 動態建立的紅隊 seat 容器打 target VM，`404`（有回應） | 通 ✓ |

**S2 Candidate Minimum 結論**：**通過**——即使把 30+20 典型人數的動態座位全部灌進去，RAM 用量也只到 12GiB 配額的 15%，遠低於 acceptance threshold 的 20% headroom 門檻（反過來說是還有 85% 沒用到）。這代表 Candidate Minimum(6C/12G) 對「典型演練規模」而言可能訂得**過於寬鬆**，不是卡在資源邊緣。

**已知量測限制**：這輪 target VM 沒有重新走「destroy → 修 partition → redefine → restart」那套精確歸戶流程（S1 各 profile 有做，S2 為了先驗證流程本身能不能走通而省略），改用 `/proc/PID/status` 的 `VmRSS` 直讀相加——數字量級可信，但沒有像 S1 那樣通過 cgroup 硬性配額驗證（也就是說沒有實測到「VM + 70 seat 容器同時被 12GiB 硬上限夾住會怎樣」，只知道理論總量遠低於上限）。6 台固定紅隊容器同樣不在任何量測範圍內（S1/S2 一致的已知缺口）。

**Low(4C/8G)、Recommended(8C/16G) 的 S2 尚未執行**——方法已驗證可重複，下一輪可直接比照 Candidate 的流程套用（含這次修好的三個 provisioner bug 與 claim 順序坑）。

## 6. Recommendation

**仍待補一部分，但已有實測依據**：Candidate Minimum(6C/12G) 在 S2 典型負載下只用了 15%，顯示這個 profile 對典型演練規模而言餘裕過大，**下限候選可能可以比 6C/12G 更低**——但 Low(4C/8G) 的 S2 這輪沒測，不能直接下結論說 4C/8G 就夠。真正的 Candidate Minimum 判定，建議下一輪把 S2 也跑一次 Low profile，用「S2 在哪個 profile 開始出現 headroom<20%」反推真正的下限，而不是繼續用 Candidate 這個中間值。Recommended(8C/16G) 維持作為建議規格候選（S1 91% headroom，S2 未測但可預期同樣寬裕）。

## 7. Validated Envelope

**已驗證**：1 host（裸機，非巢狀 VM）+ 1 target VM（Falco/Alloy/app 烤進 golden image）+ 6 台固定紅隊容器 + 完整 L1 觀測棧（含 Ollama），在 Low(4C/8G)／Candidate Minimum(6C/12G)／Recommended(8C/16G) 三個 cgroup 圈禁 profile 下的 Platform 狀態（S1）。**S2 典型演練負載（30 Red + 20 Blue，70 個動態 seat 容器）已在 Candidate Minimum(6C/12G) 上端到端驗證通過**，含真實 HTTP 建立演練流程（非模擬）、真實 seat provisioner 建置、真實網路連通性 smoke。

**未驗證**：volumetric DDoS、heavy PCAP、malware detonation、Low/Recommended 兩個 profile 的 S2、multi-host 部署、S2 狀態下 target VM 的精確 cgroup 歸戶（見 5.4 量測限制）。

## 8. Revalidation Triggers

需要重跑本 baseline 的情況：participant envelope 提高、新增重量級 service、加入 IDS/PCAP、加入 DDoS scenario、target VM 數量增加、container→VM 架構改變、single-node→multi-host、telemetry volume 模型重大改變、Ollama 模型換版或常駐推論策略改變（見 5.2 的記憶體波動註記）、seat_provisioner.py 的建置邏輯有重大改動（本輪修的三個 bug 屬於此類）。

---

## 執行狀態（供接手者參考，非正式章節）

- [x] 方法論設計並驗證可行（cgroup slice + VM domain partition 雙重圈禁，單層虛擬化，不失真）
- [x] Low(4C/8G) profile：S0 + S1
- [x] Candidate Minimum(6C/12G) profile：S0 + S1
- [x] Recommended(8C/16G) profile：S0 + S1
- [x] `#62` Seat Provisioner 接線修復並端到端驗證（另一顆 commit，同一個 PR）
- [x] `#62` seat_provisioner.py 三個真 bug 修復：blue-seat image 沒人 build、Dockerfile COPY 路徑錯 context、`cs0` peer 名孤兒擋住全部座位
- [x] `#143` 項目 1（prepare 沒有合法呼叫者）修好後（另一個 PR #145），S2 blocker 解除
- [x] Candidate Minimum(6C/12G) profile：S2（30 Red + 20 Blue，70 個動態 seat 容器，100% 建置成功，headroom 仍有 ~85%）
- [ ] Low(4C/8G)、Recommended(8C/16G) 兩個 profile 的 S2（方法已驗證可重複套用）
- [x] 第 6 節 Recommendation 已有初步依據（Candidate 過於寬鬆），但正式下限判定待 Low profile 的 S2 補齊
- [x] `.88` 每輪測試後都已還原乾淨（git checkout 復原 `docker-compose.yml`、slice 單元已移除、容器/VM/network 全部 teardown）
