# 資安攻防平台 / Cyber Range Platform

Gamified Red-Blue-Purple 攻防訓練平台。不是 observability lab，也不是 CTF ——
是可計分、可重播、可產出演練報告的攻防訓練產品。

> Agent 設定見 [CLAUDE.md](./CLAUDE.md)。本檔是人看的入口。

## 一鍵部署 ／ 一鍵測試

只有兩個指令要記得。其餘 `scripts/range/*.sh` 都由它們呼叫，不必逐支背。

```bash
sudo bash deploy.sh      # 部署：觀測平面(compose) + Range（目前已實作 G2 四區／靶機 VM／六台紅隊）
sudo bash test.sh        # 測試：單元 → compose 整合 → range 契約 → 真環境全鏈
```

常用變化：

| 指令 | 用途 |
|---|---|
| `sudo bash deploy.sh --install-deps` | **乾淨主機**：連依賴一起裝（docker / OVS / libvirt / qemu / nftables）後再部署 |
| `sudo bash deploy.sh --stack-only` | 只起觀測棧，不建 range（沒有 KVM/OVS 的機器） |
| `sudo bash deploy.sh --reset` | 先拆乾淨再部署（演練之間回到已知狀態） |
| `bash test.sh --unit-only` | 只跑不需 range 的測試 |

`deploy.sh` 開頭會跑 **preflight**：docker 是 L1 硬依賴（缺就直接失敗，不是默默半殘）；
OVS／libvirt／qemu／`/dev/kvm` 是 L2 依賴，缺就自動退成只起觀測棧並說明原因。它也會確認
**服務真的在跑**（裝了不等於起了），並回報 nested 與 kernel BTF 供判斷 Falco 模式。`test.sh` 分四層跑，**略過的項目會標明理由，不會把略過講成通過**。

首次在新機器上要先裝依賴 → [scripts/range/HOST-SETUP.md](./scripts/range/HOST-SETUP.md)。

### Falco 的兩種部署模式（自動選）

Falco 是 runtime sensor，需要驅動吃得下 host kernel。腳本用
[scripts/range/falco-mode.sh](./scripts/range/falco-mode.sh) 自動判斷，也可用
`FALCO_MODE=container|vm` 覆寫：

| 模式 | 何時採用 | Falco 跑在哪 | 說明 |
|---|---|---|---|
| `container` | host kernel 為 Falco 驅動支援的版本 | compose 的 falco 容器 | 快、單機、與觀測棧同一個 compose |
| `vm` | host kernel ≥ 7（實測驅動不相容） | golden 靶機 VM（kernel 6.8） | 事件經 target 側 Alloy 推回 Z-MGMT 的 Loki；更貼近產品形態 |

> 為什麼要分兩案（2026-08-09 大主機實測）：kernel `7.0.0-28` 上 Falco 0.39.2／0.44.1 的
> modern-eBPF 都因 CO-RE relocation 對不上而 `scap_init` 失敗（`struct mm_struct.rss_stat`
> 版面在新 kernel 變了），kmod 驅動同樣不支援。這是 Falco 驅動與前沿 kernel 的落差，
> 不是設定問題 —— 與其撞牆，不如先問 kernel 再決定走哪案。

## 產品全貌與進度

整個產品切成八個 workstream；2026-08-12 新增的
**WS8 Event Control Plane（會議中控）**負責憑據、座位與會話，並把架構擴成六區、五條跨世代契約。
依賴關係與「為什麼是這個順序」見 **SA §4.1**；下表是**目前進度**：

| WS | 工作包 | 內容 | 狀態 |
|---|---|---|---|
| **6** | Range Infrastructure | 執行環境、隔離、Reset、網路與部署 | 🟡 G2 四區 VLAN／方向性防火牆／靶機真 VM／六台紅隊／一鍵 IaC 已在單主機實測；G3 新增 Z-EDGE／Z-BLUE 與動態座位，[#20](https://github.com/Graylee0128/cyber/issues/20)（六區網路契約）與 [#62](https://github.com/Graylee0128/cyber/issues/62)（Seat Runtime，PR #116／#117／#118）**均已交付關閉** —— **WS6 已無 open 票** |
| **4-P1** | Purple Platform · Telemetry & Detection | 遙測、偵測、Response、事件 schema | 🟡 G2 前四條契約與全鏈已真環境實測；[驗收 9 項中 4 項有證據](./purple_platform_plan.md#27-p1-驗收)，source registry（原 #18）已由 PR #73 合併，餘 [#29](https://github.com/Graylee0128/cyber/issues/29)（來源欄位＋VM 時鐘）在追。response 鏈程式碼已合（PR #39），三項大主機驗證隨 [#44](https://github.com/Graylee0128/cyber/issues/44) 重烤 golden 一併補，故驗收第 2／7 項仍未打勾 |
| **4-P2** | Purple Platform · Evaluation & Console | coverage／MTTD／MTTR／缺口分類、**Purple Console** | 🟡 三張傘票 [#21](https://github.com/Graylee0128/cyber/issues/21)（Evaluation Backend）[#26](https://github.com/Graylee0128/cyber/issues/26)（Purple Console）[#28](https://github.com/Graylee0128/cyber/issues/28)（Exercise Report）與 [#90](https://github.com/Graylee0128/cyber/issues/90)（Evaluation 接線，Phase 4 的 20 次量測已在真環境跑滿，PR #111）**均已交付關閉**；Console 畫面本身由 PR #110 落地（`ui/purple/`）。[#98](https://github.com/Graylee0128/cyber/issues/98)（#26／#28 的真環境驗收）已於 2026-08-14 關閉 —— **WS4-P2 已無 open 票** |
| **5** | Cyber Range Core | Event、Score、Exercise State、API | 🟡 架構已定案（[spec](./.scratch/ws5-range-core/spec.md)，2026-08-11 grilling，5 題）；歷史里程碑 [#31](https://github.com/Graylee0128/cyber/issues/31) 已由 PR #40 交付，交付格式與 WS2 決策的六處衝突已由遷移票（原 #42，PR #54）修正並關閉，[#32](https://github.com/Graylee0128/cyber/issues/32) 起的其餘票可照現行 schema 開工 |
| **1** | Product / Game Design | 遊戲規則、流程、難度、Objective、Hint | ✅ 規則已定案（[spec](./.scratch/ws1-game-design/spec.md)，2026-08-11 grilling，8 題）。無自有程式碼產出（SA §4.2：無基礎設施足跡），決策已內嵌為 WS5（[#32](https://github.com/Graylee0128/cyber/issues/32)／[#33](https://github.com/Graylee0128/cyber/issues/33)）與 WS2 諸票的驗收條件 |
| **2** | Scenario / Target | 靶機、漏洞、攻擊鏈、Flag、Scenario Package | 🟡 內容規則已定案（[spec](./.scratch/ws2-scenario-target/spec.md)，2026-08-11 grilling，17 題）；[#43](https://github.com/Graylee0128/cyber/issues/43)（sources 重整）已由 PR #100 交付關閉，[#44](https://github.com/Graylee0128/cyber/issues/44)（真攻擊面，PR #92／#95／#99）與 [#47](https://github.com/Graylee0128/cyber/issues/47)（第一個真 scenario `shopdb-credential-pivot`，PR #112）**均已交付關閉**，schema 遷移（原 #42）已由 PR #54 關閉 —— **WS2 已無 open 票**。#47 的 T4 實測走完整條：SQLi 撈 credentials → 負向確認 webapp 對 vault 無 grant → dbadmin 直連取 flag，與 host 當場 flag 完全一致。**#65 決策 19**：個人計分 objective 只長在 Z-BLUE，scenario 需明文指定主線攻擊面 |
| **3** | Blue Operations | Incident、Investigation、Response Workflow | 🟡 藍隊工作定義已定案（[spec](./.scratch/ws3-blue-ops/spec.md)，2026-08-11 grilling，9 題）；[#48](https://github.com/Graylee0128/cyber/issues/48)（人在迴圈）[#49](https://github.com/Graylee0128/cyber/issues/49)（Investigation／遮蔽／評分）[#51](https://github.com/Graylee0128/cyber/issues/51)（封鎖路徑，PR #106／#109）**均已交付關閉** —— **WS3 已無 open 票**。#51 的驗收是跨容器 e2e：Blue `contain` → Range Core（Z-APP）→ receiver enqueue（Z-MGMT），land-then-dispatch 確保不會「有分數沒封鎖」。**關鍵發現：封鎖原本是全自動的，SA §9 四個 Blue objective 有三個是機器在做** |
| **8** | Event Control Plane（會議中控）| 憑據、座位、會話 —— `player_id` 的唯一產生點 | 🟡 **架構已定案**（[spec](./.scratch/ws8-event-control/spec.md)，2026-08-11／12 兩輪 grilling，**25 條決策**；[中控畫面 demo](./.scratch/ws8-event-control/demo.html)、[玩家旅程圖](./.scratch/ws8-event-control/player-journey-v0_1-draft.svg)）。票：[#20](https://github.com/Graylee0128/cyber/issues/20)（六區網路契約）與 [#59](https://github.com/Graylee0128/cyber/issues/59)（Admission／Seat，PR #105）**均已交付關閉**；中控畫面（原 handoff 票 [#76](https://github.com/Graylee0128/cyber/issues/76)）已由 PR #110 落地（`ui/event-control/`）。[#78](https://github.com/Graylee0128/cyber/issues/78)（承載 spike，PR #115）與 [#62](https://github.com/Graylee0128/cyber/issues/62)（Seat Runtime，PR #116／#117／#118）於 2026-08-15 交付關閉 —— **WS8 已無 open 票**。SA 回寫由 PR #68 完成；決策 gate #65 已於 2026-08-12 拍板收斂並關閉 |
| **7** | Product UI | Player Portal、Blue SOC、Battleboard、Instructor（**Purple Console 屬 4-P2**，SA §4.2）| 🟡 邊界層 [#52](https://github.com/Graylee0128/cyber/issues/52)（共用契約＋服務身分，`src/disclosure/`）與**畫面層** [#75](https://github.com/Graylee0128/cyber/issues/75)（PR #110）**均已交付關閉** —— **WS7 已無 open 票**。六個畫面在 [`ui/`](./ui/README.md)：零依賴靜態頁，服務 token 由 nginx server 端注入、不進瀏覽器，前綴決定身分→身分決定 clearance。**殘留缺口誠實記在 [ui/README.md](./ui/README.md)**（來源 IP 歸屬與反向代理相衝、教官畫面只靠網段擋、`/api/scenarios` 會吐 `attack_chain`、Override／Inject 後端無端點） |

四條線可平行：**技術線** WS4-P2、**產品線** WS1→WS5→WS7、**內容線** WS2／WS3、
**入場線** WS8→WS6→WS7。箭頭方向不可逆 —— 逆向施工的代價是重工，不是延遲（SA §4.1）。

> 入場線是 2026-08-12 新增的。它不是活動工具而是架構缺口：WS1 §1.3 已定案 Red 個人計分、
> WS5 schema 已要 `player_id`，但**目前沒有任何元件在產生它** —— 六台 kali 由
> [zones.env](./scripts/range/zones.env) 靜態配置，架構裡不存在「誰坐 kali-03」這個概念。

## 現在在做什麼

WS6 與 P1 的主體已完成並在大主機實測（2026-08-10 四層測試全綠）。
2026-08-11／12 把 **WS1／WS5／WS2／WS3／WS8 五條線的決策一次做完並切票**，
再於 2026-08-12 把細碎子票**收斂成 canonical work package**（每張分「Authoritative
blockers」＝現行依賴 ＋「Preserved sub-tickets」＝歷史證據兩段）。卡點從
「不知道要做什麼」變成「同時能做的太多」。

同日稍晚，**#65 decision gate 第二輪 grilling 十題全數拍板並關票**（詳見
[WS8 spec §10.1](./.scratch/ws8-event-control/spec.md)）。**目前沒有任何 human gate 擋著。**

Canonical open set（1 張，2026-08-15）：**#69**。
（收斂當日 22 張 → 陸續關閉 #56／#42／#18／#19／#65／#20／#21／#26／#28／#29／#32
／#33／#36／#43／#44／#47／#48／#49／#51／#52／#59／#62／#75／#76／#78／#90／#98，
新增 #69／#75／#76／#78／#90／#98。#69 是文件維護的常駐 anchor，**不因單次清理而關閉**
—— 也就是說**目前唯一 open 的票就是這張常駐 anchor 本身，實作票全部清空**。）

> 2026-08-14 單日關掉六張：#44（真攻擊面，PR #92／#95／#99）、#90（Evaluation 接線，
> PR #91／#111）、#51（封鎖路徑，PR #106／#109）、#47（第一個真 scenario，PR #112）、
> #75／#76（Product UI 與會議中控，PR #110）。#75／#76 原標 `ready-for-human` 交外部
> 負責人，維護者於當日收回自做，改標 `ready-for-agent` 後交付。
>
> **2026-08-15 把卡在硬體上的三張一次清完**：拿到 kernel 6.8 的目標主機（10.167.223.45）
> 之後，#78 承載 spike 真的量出數字（6 核／10Gi 撐到 **230 台**紅隊容器，第一個瓶頸是 RAM
> 不是 OVS port 或位址空間，PR #115），解除 #62 的 blocker；#62 隨即分三段交付
> （PR #116 紅隊 provisioner／#117 Z-BLUE image／#118 藍隊每座兩台＋seat 隔離＋host Falco
> ＋RED→BLUE DMZ-only），全部在該主機真環境驗證。#98 的項目 1、2 同樣在該主機補完並於
> 2026-08-14 關閉。
>
> **實作票已全部清空**，只剩 #69 這張常駐 anchor。下一步不再卡硬體，是要決定開哪一批新票。

- 對外契約 → [docs/p1-output-contract.md](./docs/p1-output-contract.md)｜執行導覽 → [archive/p1-output-contract-map.md](./archive/p1-output-contract-map.md)（P1 已結，存查）
- 決策 spec → [WS1 遊戲規則](./.scratch/ws1-game-design/spec.md)｜[WS2 Scenario 內容規則](./.scratch/ws2-scenario-target/spec.md)｜[WS3 藍隊工作定義](./.scratch/ws3-blue-ops/spec.md)｜[WS5 Range Core](./.scratch/ws5-range-core/spec.md)｜[WS7 Console 邊界層](./.scratch/ws7-boundary/spec.md)｜[WS8 會議中控](./.scratch/ws8-event-control/spec.md)
- **實作的畫面**（吃真 API）→ [`ui/`](./ui/README.md)：Battleboard｜Player Portal（Red／Blue 兩個獨立入口）｜Blue SOC｜Purple Console｜Instructor Console｜Event Control
- 視覺提案（**已被上面取代**，保留為設計依據；全是零依賴單檔 mock、資料寫死）→ [中控畫面](./.scratch/ws8-event-control/demo.html)｜[Purple Console](./.scratch/purple-console-ui/demo.html)｜[Battleboard](./.scratch/battleboard-ui/demo.html)｜[Player Portal](./.scratch/product-ui/player-portal.html)｜[Blue SOC](./.scratch/product-ui/blue-soc.html)｜[Instructor Console](./.scratch/product-ui/instructor-console.html)
- 所有票 → [GitHub Issues](https://github.com/Graylee0128/cyber/issues)

| 目前 open（唯一一張） | 說明 |
|---|---|
| [#69](https://github.com/Graylee0128/cyber/issues/69) Docs maintenance | canonical set 漂移同步、票關閉後的 stale 清理。**常駐 anchor，不因單次清理而關閉** —— 它同時是 `pr-contract.yml` 給純文件 PR 的唯一合法 target，關掉它之後每張文件 PR 都會被 metadata check 擋下 |

> ⚠️ 自動 enqueue 已由 [#48](https://github.com/Graylee0128/cyber/issues/48) 降級為「待處置建議」。
> T4 的封鎖鏈若靠自動 enqueue 驅動會無聲變綠 —— 現行路徑是藍隊動作經 Range Core
> 派送（`triggered_by="manual"`），測試載具才走 auto。

P1 驗收的逐項狀態見 [purple_platform_plan.md §2.7](./purple_platform_plan.md#27-p1-驗收) ——
**9 項中 4 項有實測證據**，其餘都有票在追，不是「差不多做完了」。

## 開發

Python 3.12＋，pytest。

```bash
python -m venv .venv && .venv/Scripts/activate   # Windows；Linux/mac 用 source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest             # 全部測試
python -m purple.clock.cli   # 時鐘同步檢查，退出碼 0/1/2
```

**測試一律需要 PostgreSQL**（2026-08-08 決定，不分層 —— 才不會有「本機綠、CI 紅」）。
`python -m pytest` 會在 PG 連不上時自動 `docker compose up -d postgres`；
不想自動起就設 `PURPLE_AUTO_COMPOSE=0`，或用 `PURPLE_PG_DSN` 指向既有 PG。
**沒有 PG 也沒有 Docker，測試一條都跑不了** —— 這是不分層的代價。

### 四層測試（`test.sh` 由上往下跑，跑得到哪層就跑到哪層）

| 層 | 內容 | 需要什麼 | CI |
|---|---|---|---|
| **T1** 單元／契約 | 純函式 + 管路契約（手餵 webhook） | PostgreSQL | `ci.yml`（PG service container）|
| **T2** compose 整合 | 真流量走完管線 | docker compose 全棧 | `integration.yml` |
| **T3** range 契約 | 四區方向性防火牆 + 六台紅隊 source IP 可分辨 | OVS + netns（+VM） | `range.yml`（netns 部分）|
| **T4** 真環境全鏈 | 紅隊隔真 VLAN 打靶機 VM → VM 內 Falco → Alloy → Loki → Core Event | 巢狀虛擬化 | 不在 CI |

T2 起 **vulnerable-app + Alloy + Loki + Grafana + Prometheus + receiver + evaluation-engine
+ postgres**，打真流量，驗證走完整條管線（不再手餵 webhook）：

| E2E | 證明 |
|---|---|
| SQLi → Core Event | log 路徑全通：SQLi → app log → Alloy → Loki → Grafana（唯一 engine，eval 10s）→ webhook → receiver → Core Event |
| 正常登入不觸發 SQLi 偵測 | 規則非恆真（少了這條，恆真的壞規則也會綠）|
| brute force → Core Event | metric 路徑：**OTLP `:4317` push** → Alloy → Prometheus remote_write → Grafana PromQL → T1110 |
| 攻擊停止 → resolved | lifecycle：Grafana resolved 與 firing 共用 event_id |
| Evidence API 取回上下文 | P1→P2 handoff：Core Event 落地 → 用 `event_id` 從**真 Loki** 取回上下文窗 |
| app 連不到 prometheus | OTLP 上線後 Prometheus 移出 z-target，契約 2（MGMT→TARGET 不可達）在 membership 層成立 |

T4 是整條鏈最真的一段（`tests/integration/test_falco_range_chain.py`）：

| 全鏈測試 | 證明 |
|---|---|
| `/exec` → Core Event T1059 | 紅隊容器(VLAN30) → 靶機 VM(VLAN20):80 → VM 內 Falco 抓 execve → Alloy → Loki(VLAN10) → Grafana → Core Event |
| `/readsecret` → Core Event T1005 | SA §7 **Scenario 03** 敏感檔存取，同一條鏈 |
| `/uncovered` → 偵測缺口 | 決定性測試的真環境版：Falco 抓得到但**刻意沒有 Grafana 規則覆蓋**，`detected`／`source_state`／`telemetry_present` 三個輸入全部從真環境實採 |

節點清單分三個環境：CI host 用 [config/clock-nodes.yaml](./config/clock-nodes.yaml)，
T2 容器用 `clock-nodes-compose.yaml`（直接讀秒、100ms），T4 VM 用
VM 開機自驗向 MGMT Loki 讀獨立 HTTP `Date`（request midpoint、5s 上界，結果以
boot nonce 綁定在 serial console，不需 VM 憑證）。Falco 與 Alloy 在同一 VM，共用
這個 kernel clock。
**每加一個遙測來源就要加一個節點** ——
沒列進來的節點不會被檢查，而不被檢查的時鐘遲早會漂。

P1 四欄的正規名稱為 `source_ip / destination / time / action_result`。App 對應
`source_ip / path / ts / outcome`；Falco 對應 command source IP、process command line、
event time、matched rule。Prometheus OTLP 路徑是取樣 counter，不是離散 action log，
因此明確排除四欄逐筆契約，但仍由既有 metric integration test 驗證。Alloy 與
response-agent 的 registry streams 是 transport/control-plane heartbeat，不是攻擊動作，
同樣明確排除逐 action 四欄，但保留 liveness 驗證。

## 文件入口

| 檔案 | 內容 |
|---|---|
| [資安攻防平台_系統架構設計文件_v0.1.md](./資安攻防平台_系統架構設計文件_v0.1.md) | 系統架構設計（SA），單一真相來源 |
| [purple_platform_plan.md](./purple_platform_plan.md) | 紫隊 P1／P2 工作規劃、計分模型、缺口分類 |
| [docs/adr/](./docs/adr/) | 架構決策紀錄，含 trade-off 與被放棄的選項 |
| [demo_network_topology_v0_3.svg](./demo_network_topology_v0_3.svg) | **現行架構**網路拓樸（G3 六區＋中控，已納入 #65 決策）—— 畫空間 |
| [cyber_range_platform_layered_architecture.svg](./cyber_range_platform_layered_architecture.svg) | 分層架構圖 —— 畫空間 |
| [attack_chain_sequence_v0_1.svg](./attack_chain_sequence_v0_1.svg) | **單次攻防的跨元件時序**（流程圖 B）—— 畫時間。整條鏈由 T4 守著，圖上每一步都是實作 |
| [.scratch/ws8-event-control/player-journey-v0_1-draft.svg](./.scratch/ws8-event-control/player-journey-v0_1-draft.svg) | **玩家旅程**（流程圖 A，七階段 × 六泳道）—— 畫時間。多數為草案，虛線框＝未實作 |
| [discuss.md](./discuss.md) | 最早的 dashboard 構想（歷史文件，多數已被 SA 吸收） |
| [archive/](./archive/) | 已非現行的拓樸圖與世代關係 |

## 已定案的關鍵決策

出自 [ADR 0001](./docs/adr/0001-p1-output-contract.md)，改動前請先讀那份的 trade-off：

- **Falco 是 runtime sensor，不是 alert engine。** 事件經 Loki → Grafana Alerting 才成為告警。
  這樣才存在「telemetry 有、detection 沒有」的中間狀態，偵測缺口才可觀測。
- **Grafana Alerting 是唯一 alert engine**，`eval interval` 10s。代價：單點、MTTD 有 ~10s 地板。
- **Core Event 不帶 `evidence_ref`。** 遙測細節住 P1 Alert Record，兩份用 `event_id` 對接。
- **MTTR 的終點是 response 生效**，不是 Grafana Resolved（後者另名 `containment duration`）。
- **指標只叫 `action coverage`**，`Detection Rate` 已廢除。

## 網段速查

| Zone | VLAN | 網段 | 住誰 | 實作位址 |
|---|---|---|---|---|
| Z-APP | 40 | 10.167.40.0/24 | Range Core、Player Portal、Battleboard、Blue SOC Console、Purple Console | 尚無住戶（WS5）|
| Z-MGMT | 10 | 10.167.10.0/24 | Prometheus、Loki、Grafana、Evaluation Engine | `.10` stub／`.20` 真 Loki |
| Z-TARGET | 20 | 10.167.20.0/24 | 靶機、Alloy、Falco、Response agent | 靶機 VM `.10` |
| Z-RED | 30 | 10.167.30.0/24 | kali-01…06（各自獨立 IP，不可被 SNAT 塌縮）| `.11`～`.16` |
| Z-EDGE | 50 | 10.167.50.0/24 | nginx／TLS 終結、ttyd 反向代理（零持久化） | G3 待 #20 實作 |
| Z-BLUE | 60 | 10.167.60.0/24 | 藍隊每人一段的受限 shell 主機 | G3，#62 已交付（每座 a／b 兩台，OVS flow per-pair 白名單） |

五條跨世代契約：`:3100`／`:9090`／`:4317` 三個 port、`TARGET → MGMT` 單向、
`RED → MGMT` deny all、collector 裝在 target 側，以及 `EDGE → MGMT` deny all。
前四條已由 [scripts/range/verify-range.sh](./scripts/range/verify-range.sh) 在 G2 實測（`test.sh` 的 T3）；
第五條與六區 G3 拓樸的實作驗收由 [#20](https://github.com/Graylee0128/cyber/issues/20) 承接。
方向性靠 router netns 的 nftables 真強制 —— docker network membership 只能做可達性、做不到方向。

## 狀態

Remote：[Graylee0128/cyber](https://github.com/Graylee0128/cyber)，**private**。
不在 `push-all-repos.sh` 的 `REPOS` 清單裡 —— 要納入請明講。
