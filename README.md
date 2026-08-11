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

整個產品切成七個 workstream（[SA §4](./資安攻防平台_系統架構設計文件_v0.1.md)）。
依賴關係與「為什麼是這個順序」見 **SA §4.1**；下表是**目前進度**：

| WS | 工作包 | 內容 | 狀態 |
|---|---|---|---|
| **6** | Range Infrastructure | 執行環境、隔離、Reset、網路與部署 | ✅ 四區 VLAN／方向性防火牆／靶機真 VM／六台紅隊／一鍵 IaC 已在單主機實測 |
| **4-P1** | Purple Platform · Telemetry & Detection | 遙測、偵測、Response、事件 schema | 🟡 四契約與全鏈已真環境實測；[驗收 9 項中 4 項有證據](./purple_platform_plan.md#27-p1-驗收)，餘 [#17](https://github.com/Graylee0128/cyber/issues/17) [#18](https://github.com/Graylee0128/cyber/issues/18) [#29](https://github.com/Graylee0128/cyber/issues/29) [#30](https://github.com/Graylee0128/cyber/issues/30) 在追 |
| **4-P2** | Purple Platform · Evaluation & Console | coverage／MTTD／MTTR／缺口分類、**Purple Console** | 🟡 已切票 [#21](https://github.com/Graylee0128/cyber/issues/21)–[#28](https://github.com/Graylee0128/cyber/issues/28)；#21 可立即開工，Console 兩張卡 [#26](https://github.com/Graylee0128/cyber/issues/26)／[#27](https://github.com/Graylee0128/cyber/issues/27) |
| **5** | Cyber Range Core | Event、Score、Exercise State、API | 🟡 架構已定案（[spec](./.scratch/ws5-range-core/spec.md)，2026-08-11 grilling，5 題）；已切票 [#31](https://github.com/Graylee0128/cyber/issues/31)–[#38](https://github.com/Graylee0128/cyber/issues/38)。**#31 已由 PR #40 合併，但交付格式與 WS2 決策有六處衝突** —— 遷移票 [#42](https://github.com/Graylee0128/cyber/issues/42) 應先於 #32 起的其餘票 |
| **1** | Product / Game Design | 遊戲規則、流程、難度、Objective、Hint | ✅ 規則已定案（[spec](./.scratch/ws1-game-design/spec.md)，2026-08-11 grilling，8 題）。無自有程式碼產出（SA §4.2：無基礎設施足跡），決策已內嵌為 [#31](https://github.com/Graylee0128/cyber/issues/31)–[#38](https://github.com/Graylee0128/cyber/issues/38) 的驗收條件 |
| **2** | Scenario / Target | 靶機、漏洞、攻擊鏈、Flag、Scenario Package | 🟡 內容規則已定案（[spec](./.scratch/ws2-scenario-target/spec.md)，2026-08-11 grilling，17 題）；已切票 [#42](https://github.com/Graylee0128/cyber/issues/42)–[#47](https://github.com/Graylee0128/cyber/issues/47)，**[#42](https://github.com/Graylee0128/cyber/issues/42) 建議優先**（修正已合併的 schema） |
| **3** | Blue Operations | Incident、Investigation、Response Workflow | 🟡 藍隊工作定義已定案（[spec](./.scratch/ws3-blue-ops/spec.md)，2026-08-11 grilling，9 題）；已切票 [#48](https://github.com/Graylee0128/cyber/issues/48)–[#51](https://github.com/Graylee0128/cyber/issues/51)。**關鍵發現：封鎖原本是全自動的，SA §9 四個 Blue objective 有三個是機器在做** |
| **7** | Product UI | Player Portal、Blue SOC、Battleboard、Instructor（**Purple Console 屬 4-P2**，SA §4.2）| 🟡 **邊界層**已定案（[spec](./.scratch/ws7-boundary/spec.md)，2026-08-11 grilling，4 題），票 [#52](https://github.com/Graylee0128/cyber/issues/52)／[#53](https://github.com/Graylee0128/cyber/issues/53)；**畫面層**仍未開始（數字由 4-P2／5 產生，不宜早做）|

三條線可平行：**技術線** WS4-P2、**產品線** WS1→WS5→WS7、**內容線** WS2／WS3。
箭頭方向不可逆 —— 逆向施工的代價是重工，不是延遲（SA §4.1）。

## 現在在做什麼

WS6 與 P1 的主體已完成並在大主機實測（2026-08-10 四層測試全綠）。
**技術線走 WS4-P2、產品線走 WS5**，兩條可平行（SA §4.1）。

- 對外契約 → [docs/p1-output-contract.md](./docs/p1-output-contract.md)｜執行導覽 → [archive/p1-output-contract-map.md](./archive/p1-output-contract-map.md)（P1 已結，存查）
- 遊戲規則 → [.scratch/ws1-game-design/spec.md](./.scratch/ws1-game-design/spec.md)｜Range Core 架構 → [.scratch/ws5-range-core/spec.md](./.scratch/ws5-range-core/spec.md)｜Scenario 內容規則 → [.scratch/ws2-scenario-target/spec.md](./.scratch/ws2-scenario-target/spec.md)｜藍隊工作定義 → [.scratch/ws3-blue-ops/spec.md](./.scratch/ws3-blue-ops/spec.md)｜Console 邊界層 → [.scratch/ws7-boundary/spec.md](./.scratch/ws7-boundary/spec.md)
- 所有票 → [GitHub Issues](https://github.com/Graylee0128/cyber/issues)

| 現在可動工 | 說明 |
|---|---|
| [#42](https://github.com/Graylee0128/cyber/issues/42) WS2-1 Scenario schema 遷移 | **建議最優先**。`#31` 已由 PR #40 合併，但同日 WS2 grilling 與已交付格式有六處衝突（[spec §7](./.scratch/ws2-scenario-target/spec.md)）。現在只有一份 scenario 檔、一支 loader、一組新測試 —— 等 `#32`–`#38` 照現行 schema 開工，遷移就變成連鎖成本 |
| [#21](https://github.com/Graylee0128/cyber/issues/21) P2-1 Action Registry | P2 的第一張，無阻塞。分母開演前固定，後面每個數字都靠它。清單來源由 [WS2 spec §2.3](./.scratch/ws2-scenario-target/spec.md) 定為 scenario 檔（檔案是來源、DB 是凍結後的實例）|
| [#43](https://github.com/Graylee0128/cyber/issues/43) WS2-2 scenario-sources 重整 | 現有五個 scenario id **沒有一個是 scenario**（compose only 或測試 fixture）。字串不動，改的是它們住哪個區塊 |
| [#19](https://github.com/Graylee0128/cyber/issues/19) 契約 1 port allowlist | 小而真的缺口：`TARGET→MGMT` 目前整段全開，且「非 telemetry port 應被擋」沒有測試 |
| [#18](https://github.com/Graylee0128/cyber/issues/18) source registry 生產路徑 | **PR #41 待合併**。在 P2 關鍵路徑上（卡 [#22](https://github.com/Graylee0128/cyber/issues/22)、[#27](https://github.com/Graylee0128/cyber/issues/27)）。[#43](https://github.com/Graylee0128/cyber/issues/43) 動同一批檔案，**應在 #41 合併後再開工** |
| [#17](https://github.com/Graylee0128/cyber/issues/17) response 鏈最後兩跳 | **PR #39 待合併**。要把 agent 烤進 golden；[#44](https://github.com/Graylee0128/cyber/issues/44) 也要重烤，兩者宜合併成一次。⚠️ [#48](https://github.com/Graylee0128/cyber/issues/48) 會把自動 enqueue 降級 —— 若 T4 靠它驅動，那條測試要改由測試載具觸發 |
| [#52](https://github.com/Graylee0128/cyber/issues/52) WS7-B1 共用契約套件 | 無阻塞，且**卡住 [#49](https://github.com/Graylee0128/cyber/issues/49)／[#36](https://github.com/Graylee0128/cyber/issues/36)**。遮蔽規則若各自實作，漏遮不會報錯、只會送分 |
| [#53](https://github.com/Graylee0128/cyber/issues/53) WS7-B2 服務身分 | 無阻塞。沒有它，「呼叫者無法自報 clearance」這條驗收**寫得出來但做不出來** |

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
| [demo_network_topology_v0_2_1.svg](./demo_network_topology_v0_2_1.svg) | **現行**網路拓樸（四區） |
| [cyber_range_platform_layered_architecture.svg](./cyber_range_platform_layered_architecture.svg) | 分層架構圖 |
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

## 狀態

Remote：[Graylee0128/cyber](https://github.com/Graylee0128/cyber)，**private**。
不在 `push-all-repos.sh` 的 `REPOS` 清單裡 —— 要納入請明講。
