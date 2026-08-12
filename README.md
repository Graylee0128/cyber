# 資安攻防平台 / Cyber Range Platform

Gamified Red-Blue-Purple 攻防訓練平台。不是 observability lab，也不是 CTF ——
是可計分、可重播、可產出演練報告的攻防訓練產品。

> Agent 設定見 [CLAUDE.md](./CLAUDE.md)。本檔是人看的入口。

## 一鍵部署 ／ 一鍵測試

只有兩個指令要記得。其餘 `scripts/range/*.sh` 都由它們呼叫，不必逐支背。

```bash
sudo bash deploy.sh      # 部署：觀測平面(compose) + Range(四區 VLAN/靶機 VM/六台紅隊)
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

整個產品切成八個 workstream。SA §4 目前仍寫七個 —— 2026-08-12 新增的
**WS8 Event Control Plane（會議中控）**尚未回寫進 SA，回寫票是
[#56](https://github.com/Graylee0128/cyber/issues/56)（同時處理四契約→五條、四區→六區）。
依賴關係與「為什麼是這個順序」見 **SA §4.1**；下表是**目前進度**：

| WS | 工作包 | 內容 | 狀態 |
|---|---|---|---|
| **6** | Range Infrastructure | 執行環境、隔離、Reset、網路與部署 | ✅ 四區 VLAN／方向性防火牆／靶機真 VM／六台紅隊／一鍵 IaC 已在單主機實測 |
| **4-P1** | Purple Platform · Telemetry & Detection | 遙測、偵測、Response、事件 schema | 🟡 四契約與全鏈已真環境實測；[驗收 9 項中 4 項有證據](./purple_platform_plan.md#27-p1-驗收)，餘 [#18](https://github.com/Graylee0128/cyber/issues/18) [#29](https://github.com/Graylee0128/cyber/issues/29) [#30](https://github.com/Graylee0128/cyber/issues/30) 在追。[#17](https://github.com/Graylee0128/cyber/issues/17) 程式碼已合併（PR #39）但**結案留言列的三項大主機驗證尚未回報**，故驗收第 2／7 項仍未打勾 |
| **4-P2** | Purple Platform · Evaluation & Console | coverage／MTTD／MTTR／缺口分類、**Purple Console** | 🟡 已切票 [#21](https://github.com/Graylee0128/cyber/issues/21)–[#28](https://github.com/Graylee0128/cyber/issues/28)；#21 可立即開工，Console 兩張卡 [#26](https://github.com/Graylee0128/cyber/issues/26)／[#27](https://github.com/Graylee0128/cyber/issues/27) |
| **5** | Cyber Range Core | Event、Score、Exercise State、API | 🟡 架構已定案（[spec](./.scratch/ws5-range-core/spec.md)，2026-08-11 grilling，5 題）；[#31](https://github.com/Graylee0128/cyber/issues/31) scenario 定義與載入**已交付**（PR #40），餘 [#32](https://github.com/Graylee0128/cyber/issues/32)–[#38](https://github.com/Graylee0128/cyber/issues/38)，#32 為下一張 |
| **1** | Product / Game Design | 遊戲規則、流程、難度、Objective、Hint | ✅ 規則已定案（[spec](./.scratch/ws1-game-design/spec.md)，2026-08-11 grilling，8 題）。無自有程式碼產出（SA §4.2：無基礎設施足跡），決策已內嵌為 [#31](https://github.com/Graylee0128/cyber/issues/31)–[#38](https://github.com/Graylee0128/cyber/issues/38) 的驗收條件 |
| **2** | Scenario / Target | 靶機、漏洞、攻擊鏈、Flag、Scenario Package | 🟡 已切票 [#42](https://github.com/Graylee0128/cyber/issues/42)–[#47](https://github.com/Graylee0128/cyber/issues/47)；#42 schema 遷移有 PR #54 在審。#44（真攻擊面）要重烤 golden |
| **3** | Blue Operations | Incident、Investigation、Response Workflow | 🟡 已切票 [#48](https://github.com/Graylee0128/cyber/issues/48)–[#51](https://github.com/Graylee0128/cyber/issues/51)：人在迴圈、technique 欄位級遮蔽、五個動作的封閉列舉、封鎖路徑 |
| **8** | Event Control Plane（會議中控）| 憑據、座位、會話 —— `player_id` 的唯一產生點 | 🟡 spec draft（[spec](./.scratch/ws8-event-control/spec.md)、[中控畫面 demo](./.scratch/ws8-event-control/demo.html)），已切票 [#56](https://github.com/Graylee0128/cyber/issues/56)–[#63](https://github.com/Graylee0128/cyber/issues/63)；三個待決在 [#65](https://github.com/Graylee0128/cyber/issues/65) 待拍板 |
| **7** | Product UI | Player Portal、Blue SOC、Battleboard、Instructor（**Purple Console 屬 4-P2**，SA §4.2）| ⬜ 主體未開始（要顯示的數字由 4-P2／5 產生，不宜早做）。已有[視覺提案](./.scratch/product-ui/spec.md)（[#55](https://github.com/Graylee0128/cyber/issues/55)）＋ 兩張不依賴數字的基礎票 [#52](https://github.com/Graylee0128/cyber/issues/52)（共用遮蔽契約）[#53](https://github.com/Graylee0128/cyber/issues/53)（服務身分）|

四條線可平行：**技術線** WS4-P2、**產品線** WS1→WS5→WS7、**內容線** WS2／WS3、
**入場線** WS8→WS6-y。箭頭方向不可逆 —— 逆向施工的代價是重工，不是延遲（SA §4.1）。

> 入場線是 2026-08-12 新增的。它不是活動工具而是架構缺口：WS1 §1.3 已定案 Red 個人計分、
> WS5 schema 已要 `player_id`，但**目前沒有任何元件在產生它** —— 六台 kali 由
> [zones.env](./scripts/range/zones.env) 靜態配置，架構裡不存在「誰坐 kali-03」這個概念。

## 現在在做什麼

WS6 與 P1 的主體已完成並在大主機實測（2026-08-10 四層測試全綠）。
2026-08-11／12 把 **WS1／WS5／WS2／WS3／WS8 五條線的決策一次做完並切票** ——
目前 43 張 open issue，卡點從「不知道要做什麼」變成「同時能做的太多」。

- 對外契約 → [docs/p1-output-contract.md](./docs/p1-output-contract.md)｜執行導覽 → [archive/p1-output-contract-map.md](./archive/p1-output-contract-map.md)（P1 已結，存查）
- 決策 spec → [WS1 遊戲規則](./.scratch/ws1-game-design/spec.md)｜[WS5 Range Core](./.scratch/ws5-range-core/spec.md)｜[WS8 會議中控](./.scratch/ws8-event-control/spec.md)
- 視覺提案（全是零依賴單檔 mock、資料寫死）→ [中控畫面](./.scratch/ws8-event-control/demo.html)｜[Purple Console](./.scratch/purple-console-ui/demo.html)｜[Battleboard](./.scratch/battleboard-ui/demo.html)｜[Player Portal](./.scratch/product-ui/player-portal.html)｜[Blue SOC](./.scratch/product-ui/blue-soc.html)｜[Instructor Console](./.scratch/product-ui/instructor-console.html)
- 所有票 → [GitHub Issues](https://github.com/Graylee0128/cyber/issues)

| 先做（需要人拍板的） | 說明 |
|---|---|
| [#65](https://github.com/Graylee0128/cyber/issues/65) WS8 三個待決 | 卡住 WS8 大半條線。其中「50+ 的實體承載」建議**先實測再開 [#62](https://github.com/Graylee0128/cyber/issues/62)** —— 撐不住會逼出多主機，那會動到 OVS trunk 設計 |
| [#64](https://github.com/Graylee0128/cyber/issues/64) WS1 §1.3 回改 | Z-BLUE 拍板後「Blue 側不做個人化」已失效，藍隊有自己的機器 |
| [#56](https://github.com/Graylee0128/cyber/issues/56) SA 回寫 | 四契約→五條、四區→六區、七 WS→八。SA 是單一真相來源，放著會讓所有下游讀到過期的區數 |

| 現在可動工（無阻塞） | 說明 |
|---|---|
| [#21](https://github.com/Graylee0128/cyber/issues/21) P2-1 Action Registry | P2 的第一張。分母開演前固定，後面每個數字都靠它 |
| [#32](https://github.com/Graylee0128/cyber/issues/32) WS5-2 Exercise 生命週期 | #31 已交付，這是 WS5 的下一張 |
| [#19](https://github.com/Graylee0128/cyber/issues/19) 契約 1 port allowlist | 小而真的缺口：`TARGET→MGMT` 目前整段全開，且「非 telemetry port 應被擋」沒有測試。契約 5 也要同一套 nft 規則，**先把規則抽成單一定義處**會讓 [#58](https://github.com/Graylee0128/cyber/issues/58) 省一次重工 |
| [#18](https://github.com/Graylee0128/cyber/issues/18) source registry 生產路徑 | 在 P2 關鍵路徑上（卡 [#22](https://github.com/Graylee0128/cyber/issues/22)、[#27](https://github.com/Graylee0128/cyber/issues/27)），PR #41 在審 |
| [#52](https://github.com/Graylee0128/cyber/issues/52) 共用遮蔽契約 | WS7 唯一不等數字的一張：遮蔽規則不能有兩份 |

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

節點清單在 [config/clock-nodes.yaml](./config/clock-nodes.yaml)。**每加一個遙測來源就要加一個節點** ——
沒列進來的節點不會被檢查，而不被檢查的時鐘遲早會漂。

## 文件入口

| 檔案 | 內容 |
|---|---|
| [資安攻防平台_系統架構設計文件_v0.1.md](./資安攻防平台_系統架構設計文件_v0.1.md) | 系統架構設計（SA），單一真相來源 |
| [purple_platform_plan.md](./purple_platform_plan.md) | 紫隊 P1／P2 工作規劃、計分模型、缺口分類 |
| [docs/adr/](./docs/adr/) | 架構決策紀錄，含 trade-off 與被放棄的選項 |
| [demo_network_topology_v0_2_1.svg](./demo_network_topology_v0_2_1.svg) | **現行**網路拓樸（四區）—— 畫空間 |
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

> SA v0.1 的表把 Z-RED 寫成 `10.167.223.0/24`；實作統一採「第三段＝VLAN id」的慣例，
> 故為 `10.167.30.0/24`。兩者指同一個區，只是位址慣例對齊。

四條跨世代契約：`:3100`／`:9090`／`:4317` 三個 port、`TARGET → MGMT` 單向、
`RED → MGMT` deny all、collector 裝在 target 側。四條都由
[scripts/range/verify-range.sh](./scripts/range/verify-range.sh) 實測（`test.sh` 的 T3），
方向性靠 router netns 的 nftables 真強制 —— docker network membership 只能做可達性、做不到方向。

> **上表與四條契約都是「已實作」的現況。** WS8 草案要把它擴成六區
> （＋Z-EDGE VLAN 50、＋Z-BLUE VLAN 60）與五條契約（＋`EDGE → MGMT` deny all），
> 見 [WS8 spec §5](./.scratch/ws8-event-control/spec.md)、
> [六區草案拓樸](./.scratch/ws8-event-control/topology-v0_3-draft.svg)、
> 票 [#58](https://github.com/Graylee0128/cyber/issues/58)／[#63](https://github.com/Graylee0128/cyber/issues/63)。
> **一條都還沒實作**，所以沒有寫進上表 —— 契約表只列 T3 驗得出來的。

## 狀態

Remote：[Graylee0128/cyber](https://github.com/Graylee0128/cyber)，**private**。
不在 `push-all-repos.sh` 的 `REPOS` 清單裡 —— 要納入請明講。
