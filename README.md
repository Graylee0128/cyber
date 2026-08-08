# 資安攻防平台 / Cyber Range Platform

Gamified Red-Blue-Purple 攻防訓練平台。不是 observability lab，也不是 CTF ——
是可計分、可重播、可產出演練報告的攻防訓練產品。

> Agent 設定見 [CLAUDE.md](./CLAUDE.md)。本檔是人看的入口。

## 現在在做什麼

**Purple Platform P1（Telemetry & Detection Pipeline）**，以 TDD 進行。

- Step 0 對外契約已定版 → [.scratch/p1-output-contract/spec.md](./.scratch/p1-output-contract/spec.md)
- 執行導覽 → [.scratch/p1-output-contract/map.md](./.scratch/p1-output-contract/map.md)
- 13 張票 → [GitHub Issues](https://github.com/Graylee0128/cyber/issues)
- 票 01 **已完成**（[#1](https://github.com/Graylee0128/cyber/issues/1)）、票 02a 測試載具**已完成**（[#2](https://github.com/Graylee0128/cyber/issues/2)），下一張是 02b（[#3](https://github.com/Graylee0128/cyber/issues/3)）

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

### 兩種測試

| 種類 | 指令 | 需要什麼 | CI |
|---|---|---|---|
| 單元／契約 | `pytest -m "not integration"` | 只要 PostgreSQL | `ci.yml`（PG service container）|
| 部署 E2E | `docker compose up -d --build --wait` 後 `pytest -m integration` | docker compose 全棧 | `integration.yml` |

部署測試起 **vulnerable-app + Alloy + Loki + Grafana + Prometheus + receiver + postgres**，
打真 SQLi，驗證走完整條管線：
**SQLi → app log → Alloy → Loki → Grafana（唯一 alert engine，eval 10s）→ webhook
→ receiver → Core Event**。這是 02b/03 契約測試的真實版本（不再手餵 webhook）。

四區 VLAN／macvlan／6 台 kali 的網段拓樸 compose 無法忠實模擬，仍屬
workstream 6 / [#13](https://github.com/Graylee0128/cyber/issues/13)。

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

| Zone | VLAN | 網段 | 住誰 |
|---|---|---|---|
| Z-APP | 40 | 10.167.40.0/24 | Range Core、Player Portal、Battleboard、Blue SOC Console、Purple Console |
| Z-MGMT | 10 | 10.167.10.0/24 | Prometheus、Loki、Grafana、Evaluation Engine |
| Z-TARGET | 20 | 10.167.20.0/24 | 靶機、Alloy、Falco、Response agent |
| Z-RED | 30 | 10.167.223.0/24 | kali-01…06（macvlan，各自獨立 IP） |

四條跨世代契約：`:3100`／`:9090`／`:4317` 三個 port、`TARGET → MGMT` 單向、
`RED → MGMT` deny all、collector 裝在 target 側。

## 狀態

Remote：[Graylee0128/cyber](https://github.com/Graylee0128/cyber)，**private**。
不在 `push-all-repos.sh` 的 `REPOS` 清單裡 —— 要納入請明講。
