# Cyber Technical Handbook

> 給開發與維運工程師。這份文件回答的不是「怎麼使用 Cyber」，而是 **Cyber 到底是怎麼做出來的**。
>
> 新進工程師從這裡進來。玩家看 [Participant Guide](../participant-guide/README.md)，
> 現場工作人員看 [Operator Guide](../operator-guide/README.md)。
>
> **文件界線**：Handbook 寫「現在怎麼運作」；`.scratch/<workstream>/spec.md` 寫「這個功能應該
> 怎麼做」；[`docs/adr/`](../adr/) 寫「為什麼這樣決定、放棄了什麼」。三者不重複。

## 1. System Overview

Cyber 是 **Gamified Red-Blue-Purple Cyber Range**——可計分、可重播、可產出演練報告的攻防
訓練產品。不是 observability lab，不是 CTF。

系統架構的單一真相來源是
[`資安攻防平台_系統架構設計文件_v0.1.md`](../../資安攻防平台_系統架構設計文件_v0.1.md)（SA）。
本文件是它的工程視角導覽，衝突時以 SA 為準。

`src/` 四個 package：

| Package | 職責 |
|---|---|
| [`src/admission/`](../../src/admission/) | 入場、座位池、教官 session、逾時清理 |
| [`src/disclosure/`](../../src/disclosure/) | 共用的身分 / clearance / 欄位遮蔽契約 |
| [`src/purple/`](../../src/purple/) | 遙測接收、評估引擎、Evidence API、Battleboard 消毒、報告 |
| [`src/range_core/`](../../src/range_core/) | 場次生命週期、計分、Objective、flag、藍隊動作、事件 SSE |

工作以 **8 個 Workstream**（WS1–WS8）切分，見 SA §4。

## 2. Architecture

repo 沒有把「Control / Data / Access / Evaluation Plane」定義成正式分類法——正式結構是
**8 Workstream × 6 網段**。以下是把既有結構映射到 plane 語彙的導覽，方便理解，但引用時
請引 Workstream：

| Plane（導覽用） | 對應 | 住哪 |
|---|---|---|
| **Control** | WS5 Cyber Range Core：場次生命週期、計分、Objective | Z-APP |
| **Data / Telemetry** | WS4-P1：Falco / Alloy / Loki / Prometheus | Z-TARGET → Z-MGMT |
| **Evaluation** | WS4-P2：Evaluation Engine ＋ Purple Console | Z-MGMT → Z-APP |
| **Access** | WS8 Event Control Plane：Admission、座位、session、gateway token 交換 | Z-EDGE → Z-APP |

## 3. Network Architecture

六個 VLAN 區，權威定義在 SA §12。

| Zone | VLAN | 網段 | 住誰 |
|---|---|---|---|
| Z-APP | 40 | 10.167.40.0/24 | Range Core、Player Portal、Battleboard、Blue SOC、Purple Console |
| Z-MGMT | 10 | 10.167.10.0/24 | Prometheus、Loki、Grafana、Evaluation Engine |
| Z-TARGET | 20 | 10.167.20.0/24 | 靶機 VM、Alloy、Falco、response agent |
| Z-RED | 30 | 10.167.30.0/24 | kali-01…06，各自獨立 IP（不可被 SNAT 塌縮） |
| Z-EDGE | 50 | 10.167.50.0/24 | nginx / TLS 終結、ttyd 反向代理，**零持久化** |
| Z-BLUE | 60 | 10.167.60.0/24 | 藍隊每人一段的受限 shell 主機 |

**五條跨世代契約**（改動前先讀 SA §12.2）：

1. 遙測只走三個 port：`:3100` Loki、`:9090` Prometheus、`:4317` OTLP gRPC
2. `TARGET → MGMT` 單向（agent-pull，不是 push）
3. `RED → MGMT` deny all
4. collector（Alloy / Falco / response agent）裝在 target 側，不在 mgmt 側
5. `EDGE → MGMT` deny all

**方向性靠 router netns 的 nftables 真強制**——docker network membership 只能做可達性、
做不到方向。實作在 [`scripts/range/`](../../scripts/range/)，驗證在
[`scripts/range/verify-range.sh`](../../scripts/range/verify-range.sh)（`test.sh` 的 T3）。

世代演進：G0（無觀測平面）→ G1 三區 → G2 四區（已實作 baseline）→ **G3 六區＋Z-EDGE/Z-BLUE
＋Admission（現行）** → G4 k8s（未建）。歷史拓樸見 [`archive/`](../../archive/)。

## 4. Runtime Architecture

### Seat Provisioner

[`scripts/range/seat_provisioner.py`](../../scripts/range/seat_provisioner.py) 是 host 側常駐
程序，**輪詢** `GET /admission/seats/pending`：

```
Admission 寫入 seat(state=requested)
        ↓ provisioner 輪詢
建置容器 / 網路
        ↓
POST /admission/seats/{id}/ready
```

**為什麼是 pull 不是 push**：push 模型等於讓 Z-APP 的服務直接驅動 host 的 OVS 腳本，
那會把 host 控制權交到應用層手上（WS5 spec §2.3）。

### Seat 形態

| | 容器數 | 命名 | 用途 |
|---|---|---|---|
| Red Seat | 1 | `seat-red-*` | Kali 攻擊機 |
| Blue Seat | 2 | `seat-blue-*` | `a` = DMZ、`b` = 內網（flag 主機） |

**估算資源時「N 人」≠「N 容器」。** ttyd 在 7681。映像 `purplescope/red-attacker`、
`purplescope/blue-seat`，Dockerfile 在 [`deploy/`](../../deploy/)。

失敗處理刻意不在 provisioner 裡——靠 Admission 的
[`sweeper.py`](../../src/admission/store/) `expire_requested()` 回收重試。

### 隔離

每座位的 OVS port 設 `protected=true`，紅隊之間再加 `iptables -I DOCKER-USER ... -j DROP`。
`RED → BLUE` 只開 DMZ。

### Target VM

golden VM 由 [`scripts/range/build-golden-target.sh`](../../scripts/range/) 建置，kernel 6.8，
Falco 規則直接烘進映像（與 compose 版共用
[`deploy/falco/rules.d/purplescope.yaml`](../../deploy/falco/rules.d/purplescope.yaml)）。

## 5. UI Architecture

七個對人畫面，零依賴靜態頁（無 build step、無 npm），nginx 服務並代理後端。

| 畫面 | 目錄 | gateway 身分 |
|---|---|---|
| Battleboard | [`ui/battleboard/`](../../ui/battleboard/) | `red`（clearance 0） |
| Player Portal — Red | `ui/player/index.html` | `red` |
| Player Portal — Blue | `ui/player/blue.html` | `blue` |
| Blue SOC Console | [`ui/blue-soc/`](../../ui/blue-soc/) | `blue` |
| Purple Console | [`ui/purple/`](../../ui/purple/) | `purple` |
| Instructor Console | [`ui/instructor/`](../../ui/instructor/) | `instructor` |
| Event Control | [`ui/event-control/`](../../ui/event-control/) | `instructor` → Admission |

**誰能看什麼、由什麼強制**：見
[Role × UI × Permission Matrix](../architecture/role-ui-permission-matrix.md)。本節不重述。

**為什麼零依賴**：六份視覺提案本來就是零依賴單檔；這是純 Python repo，加一套 Node
toolchain 要同時進 CI、進 image、進 range 主機；這些畫面是讀多寫少的表格與清單。
改用框架要等到有一個「靜態頁做不到」的具體需求，不是預先假設。

**一個頁面只能有一個身分。** 紅藍玩家視角是兩個檔案而不是同一頁的兩個分頁——分頁式
做法要讓一頁同時持有 red 與 blue 兩個 gateway，等於給紅隊一個「切一下就升級可見範圍」
的按鈕。切換身分 ＝ 換頁。

實作導覽與完成度對照見 [`ui/README.md`](../../ui/README.md)。

## 6. Identity / Authorization

### 這一層的地基

**token 從不進瀏覽器。**

```
瀏覽器  ──不帶 Authorization──▶  nginx gateway  ──貼上該前綴的 token──▶  後端
        /gw/blue/core/api/score                   Bearer <blue token>
```

前綴決定身分，身分決定 clearance，clearance 決定後端遮掉哪些欄位。
**遮蔽發生在後端組裝回應時**，不是前端渲染時——前端遮是假的，devtools 打開就看到。

權限邊界的實體就是那個檔案：
[`deploy/ui/default.conf.template`](../../deploy/ui/default.conf.template)。

### disclosure.identity

[`src/disclosure/identity.py`](../../src/disclosure/identity.py) 是 Range Core 與 Evidence API
共用的 token → 身分交換，**識別身分只有這一條路**：

- token 由部署時的環境變數注入（`<PREFIX><IDENTITY_UPPER>`，例如 `RANGE_CORE_TOKEN_RED`）
- 只讀 `Authorization: Bearer <token>`，**任何客戶端可設的身分欄位一律不採信**
- 對不上的 token → `None` → fail closed，**沒有預設身分**

### Clearance

[`src/disclosure/clearance.py`](../../src/disclosure/clearance.py)：
`red=0 / blue=1 / purple=2 / instructor=3`，線性階層 `public ⊂ blue ⊂ purple ⊂ instructor`。
同時用於端點門檻（`ENDPOINT_MIN_CLEARANCE`）與 SSE 事件的欄位遮蔽。

### Admission

session 相關的三條 `auth_request` 端點：

| 端點 | 回答 |
|---|---|
| `/admission/auth/instructor` | 這個瀏覽器有沒有登入教官 |
| `/admission/auth/seat` | 這個 session 坐哪台機器（回 `X-Seat-Source-Ip`） |
| `/admission/auth/ttyd/{terminal}` | 這個 session 擁不擁有這台終端機 |

### 名冊歸屬（ADR 0004）

`POST /api/submissions` 與 `/api/hints` 用來源 IP 當名冊的鍵，而 Range Core 的 `_source_ip`
**刻意不信任** `X-Forwarded-For`——信了就等於讓紅隊玩家用別人的身分提交 flag。

解法不是「開始相信某個標頭」，而是**只信任一個呼叫者**：gateway 先 `auth_request` 問
Admission「這個 session 坐哪台機器」，把答案放進 `X-Seat-Source-Ip`；Range Core 只在
TCP peer 真的是 `RANGE_CORE_TRUSTED_EDGE_HOST` 解析出的位址時才採信。玩家直連 Range Core
自己塞標頭沒有用——他的 peer 是自己那台 kali。

**為什麼由 gateway 而不是 Z-EDGE**：代宣告來源 IP 需要握有 Range Core 的服務 token，而
Z-EDGE 必須維持零憑證（WS8 spec §5.3）。詳見
[ADR 0004](../adr/0004-roster-attribution-via-trusted-gateway.md)。

## 7. Telemetry

```
Falco ──▶ Alloy ──▶ Loki ──▶ Grafana Alerting ──▶ receiver ──▶ Core Event
                                                                    │
                                                                    ▼
                                                            Evaluation Engine
```

| 元件 | 設定 | 說明 |
|---|---|---|
| **Falco** | [`deploy/falco/rules.d/purplescope.yaml`](../../deploy/falco/rules.d/purplescope.yaml) | 只有 4 條自訂規則，不載預設 ruleset。寫 JSON 到 `/var/log/falco/events.json` |
| **Alloy** | [`deploy/alloy/config.alloy`](../../deploy/alloy/config.alloy) | 三條路徑：app log → Loki、Falco events → Loki、OTLP `:4317` → Prometheus remote_write |
| **Loki** | [`deploy/loki/loki-config.yaml`](../../deploy/loki/) | filesystem、single-binary、保留 168h |
| **Grafana** | [`deploy/grafana/provisioning/alerting/`](../../deploy/grafana/) | **唯一 alert engine**，eval interval 10s |
| **receiver** | `src/purple/receiver/` | 接 Grafana alert webhook → Core Event / Alert Record |

### 兩個必須守住的設計

**Falco 是 runtime sensor，不是 alert engine。** 事件要經 Loki → Grafana Alerting 才成為告警。
這樣才存在「telemetry 有、detection 沒有」的中間狀態——**偵測缺口才可觀測**。
規則集裡的 `PurpleScope Uncovered Action` 是刻意不告警的，用來證明這個中間狀態是真的。

**Grafana 是單點。** 它掛了偵測全停，而且不會有任何告警通知你。目前的補償只有 gateway 上
一條無身分限制的 liveness passthrough（`/health/grafana`），Instructor Console 拿它點一顆
狀態燈——**那不是告警冗餘**。代價還包含 MTTD 有 ~10 秒地板。

## 8. Range Core

[`src/range_core/api.py`](../../src/range_core/api.py)，FastAPI。所有端點經
`require_identity` 依賴取得身分。

| 端點 | 說明 |
|---|---|
| `GET /api/scenarios` | 公開，**後端剝除 `attack_chain` 與 hint 內文**（#126 P0 修正） |
| `POST /api/exercises/start` / `prepare` / `reset` | 生命週期；`prepare` 只接受 Admission 服務身分 |
| `PUT/GET/DELETE /api/exercises/{id}/players/{pid}` | 名冊發布，Admission 專用 |
| `POST /api/submissions` | flag 提交，名冊以來源 IP 為鍵 |
| `GET/POST /api/hints` | 價格 / 內文（購買才記錄扣分） |
| `POST /api/objectives/sync` | 依 Core Event 重掃遙測型 Objective |
| `POST /api/blue-actions` | 藍隊動作，clearance 1；`CONTAIN` 會實際派送到 Z-MGMT |
| `GET /api/score` | 紅隊（`scoring.py`）＋ 藍隊（`blue_scoring.py`），只算在籍玩家 |
| `GET /api/events/live` | SSE，逐 clearance 過濾，`Last-Event-ID` 續傳，連線壽命上限 300s |

### 計分的兩個原則

- **hint 扣分取最大值，不是累加**（ADR 0002）。
- **`contain` 記錄 `dispatch_status`，未實際派送不給分**——絕不顯示一個沒發生的封鎖所帶來
  的分數。

藍隊動作**只有團隊身分，沒有個人歸屬**（WS3 spec §5.1）。

scenario 內容在 [`scenarios/`](../../scenarios/)。

## 9. Evaluation Engine

規劃見 [`purple_platform_plan.md`](../../purple_platform_plan.md) §3。兩個元件：
**Evaluation Engine**（Z-MGMT，算指標）與 **Purple Console**（Z-APP，呈現）。

### 指標

```
action coverage   = hit(C1+) / (hit + miss)
confirmation rate = C3 / (hit + miss)
```

**指標只叫 `action coverage`，`Detection Rate` 已廢除**（分母有歧義，ADR 0001 ⑨）。

證據三級：**C1** 攻擊嘗試有 log ／ **C2** 攻擊到達目標 ／ **C3** 跨來源互證。

### 兩種延遲不可混用

- **MTTD**：alert 觸發
- **MTTR**：**response 生效**（真正的 Respond）
- **containment duration**：alert → resolved（Grafana Resolved，攻擊停了但不見得是藍隊做的）

### 兩種 miss 必須分開

| 現象 | 分類 | 要補什麼 |
|---|---|---|
| 完全沒有 log | **visibility gap** | 收集來源 |
| 有 log 但沒有規則 | **detection gap** | 偵測規則 |

這是 Evaluation Engine 需要原始 log 查詢權限的原因。Purple Console 的逐來源標記
✅ / ❌ / — 對應「有事件 / 已部署但無事件 / 未部署」，**❌ 與 — 永遠不能混為一談**。

**Battleboard 不屬於 Purple**（`purple_platform_plan.md` §3.9）——它的觀眾包含紅隊，
歸 WS7 Product UI。消毒實作在 `src/purple/battleboard/sanitize.py`。

## 10. AI Assistance

本機推論，**不呼叫任何外部 API**。三塊已落地
（[#131](https://github.com/Graylee0128/cyber/issues/131) /
[#132](https://github.com/Graylee0128/cyber/issues/132) /
[#133](https://github.com/Graylee0128/cyber/issues/133)）：

| 元件 | 位置 | 作用 |
|---|---|---|
| Ollama 基礎設施 | compose `ollama` service（`ollama/ollama:0.3.14`） | 模型落 `ollamadata` volume，**不烤進 image**，容器重建不用重拉 |
| 推論 client | `src/purple/ai/ollama_client.py` | 統一出入口 |
| Exercise Report 敘事 | `src/purple/report/narrative.py` | 把評估結果寫成敘事段落 |
| Instructor SOC Copilot | `src/purple/evidence/copilot.py` | 把 Admission 告警唸成一段 AI 摘要 |

兩條約束：

- **Copilot 只給 Instructor，不給 Red。**
- **純呈現層**——不寫回任何計分／證據欄位。AI 服務沒起或逾時時畫面空著，其餘功能不受影響。

healthcheck 只驗「server 有回應」（`ollama list` 成功），**不等模型真的拉完**。

## 11. Deployment

單機部署。入口：

| 檔案 | 作用 |
|---|---|
| [`bootstrap.sh`](../../bootstrap.sh) | clone / 更新 repo 到 `~/cyber` 後呼叫 `deploy.sh` |
| [`deploy.sh`](../../deploy.sh) | 單一部署入口，L1 ＋ L2 兩層 |

- **L1 觀測／評估平面**（compose）：永遠起。docker 是硬依賴，缺就直接失敗，不是默默半殘。
- **L2 Range**：OVS VLAN ＋ nftables ＋ 靶機 VM ＋ 紅隊容器。需要 KVM / libvirt / OVS，
  缺了自動退成只起觀測棧並說明原因。

旗標：`--install-deps` / `--stack-only` / `--reset`。

compose profile：**預設**（遙測棧）、`falco`、`admission-e2e`（Admission ＋ UI ＋ evaluation-api）。
**`deploy.sh` 預設不會起 UI**——UI 在 `admission-e2e`。

> ⚠️ 兩個 profile 各有自己的 Postgres，access plane 的 event id 對預設 profile 的
> `evaluation-engine` 不可見。

實際操作步驟見 [Operator Guide §3](../operator-guide/README.md#3-deploy-cyber)。

## 12. Hardware / Capacity

**Minimum / Recommended 規格待 [#137](https://github.com/Graylee0128/cyber/issues/137) 產出。**
在那之前不要在任何文件寫死規格數字。

已有的證據是 [#78](https://github.com/Graylee0128/cyber/issues/78) 的 capacity spike：
6C / 10 GiB / 97 GB VirtualBox VM 上，完整 stack ＋ 靶機 VM 共存時 70 容器約 3.7 GiB RAM，
推到 230 容器才碰 RAM 瓶頸（第一個瓶頸是 RAM，不是 CPU）。

**這是承載證據，不等於 production minimum**：#78 用 VirtualBox 巢狀虛擬化，且 seat 容器
起的是 `sleep infinity`，約 0.5 秒只是網路掛載的下限，不是完整 seat provisioner 的真實開銷。

## 13. Security Boundaries

### 守得住的

- token 不進瀏覽器，前綴決定身分，欄位遮蔽在後端
- `disclosure.identity` fail closed，無預設身分
- Battleboard 的 `revealed` 由 nginx 前綴寫死，呼叫端說了不算
- 名冊歸屬只信任單一 gateway（ADR 0004）
- Z-EDGE 零憑證
- 紅隊之間 OVS protected port ＋ `DOCKER-USER` DROP

### 已知缺口

| # | 缺口 | 出處 |
|---|---|---|
| 1 | **`/gw/blue/` 前綴沒有任何身分檢查**——知道網址就能取得 clearance 1 資料並送出藍隊動作 | [matrix §6](../architecture/role-ui-permission-matrix.md#6-已知缺口) |
| 2 | **存取層沒有獨立的 Purple 身分**——紫隊必須用教官 session 登入 | matrix §6 |
| 3 | **`UI_PRIVILEGED_CIDR` 預設 `0.0.0.0/0`**，正式部署必須收斂到 Z-MGMT | `ui/README.md` 缺口 2、[#126](https://github.com/Graylee0128/cyber/issues/126) |
| 4 | **Evaluation API 自己不驗證身分**，安全性依賴「只住 Z-MGMT 不對外」這個假設 | matrix §6 |
| 5 | briefing 沒有 API——`scenarios/<id>/briefing.md` 沒有 HTTP 出口 | `ui/README.md` 缺口 5 |
| 6 | 未登記在 `config/scenario-sources.yaml` 的 scenario 在 Evaluation API 上回 503（已攔截，非未處理的 500）；`expected_sources` 有 scenario `metadata.yaml` 與該清單兩個真相來源要手動保持一致 | `ui/README.md` 缺口 6 |
| 7 | 現場進場碼 UI 是純前端假資料，未接上真正的 HMAC 驗證 | `src/admission/templates/event_control.html` |
| 8 | `join.html` 沒有任何 script，不會呼叫 claims API——是呈現用範本，不是可用表單 | 同上 |
| 9 | 跨 profile Postgres 分裂 | `ui/README.md` |

**沒有 Override Score / Inject Event 端點**——不是缺口，是 2026-08-15 的決議：在有真實
教官需求出現前不做。

## 14. Failure / Recovery

| 失效 | 表現 | 回復 |
|---|---|---|
| Grafana 掛掉 | **偵測全停且無任何告警** | Instructor Console 狀態燈是唯一信號；重啟 grafana |
| Falco 驅動不相容 | `scap_init` 失敗 | 切 `FALCO_MODE=vm`（golden VM kernel 6.8） |
| 座位卡在 requested | 玩家進不來 | Admission sweeper `expire_requested()`；或 Event Control 手動清理 |
| response 派送失敗 | `dispatch_status != dispatched` | 動作不計分（刻意）；查 Z-MGMT 佇列 |
| session 綁錯瀏覽器 | 終端機 403 | Event Control「重新綁定會話」 |
| 場次要重來 | — | `POST /api/exercises/reset`（保留稽核）或 `deploy.sh --reset`（整台重建） |

## 15. Development

### Local environment

```bash
python -m venv .venv
pip install -e ".[dev]"
python -m pytest
```

Python ≥ 3.12。`testpaths=tests`、`pythonpath=src`。

**測試永遠需要 PostgreSQL**：`pytest` 會自動 `docker compose up -d postgres`
（`PURPLE_AUTO_COMPOSE=0` 可關）。**刻意不分層**——才不會有本機綠、CI 紅。

兩個 marker：`environment`（對真實環境斷言，不是 red-green-refactor）、
`integration`（需完整 compose stack，只在 integration workflow 跑）。

### Tests

[`test.sh`](../../test.sh) 是單一入口，四層由上往下：

| 層 | 內容 | 需要 |
|---|---|---|
| T1 | 單元 / 契約 | Postgres |
| T2 | compose 整合 | docker |
| T3 | range 契約 | OVS / netns |
| T4 | 真環境全鏈 | 巢狀虛擬化 |

**被略過的層會標明理由，不會把略過講成通過。**

### CI

[`.github/workflows/`](../../.github/workflows/)：

| Workflow | 作用 |
|---|---|
| `ci.yml` | 單元 ＋ shell lint。取消被取代的 PR run，但保留每個 master commit 的結果 |
| `integration.yml` | 完整 compose 部署 ＋ 真 SQLi 流量測試。docs-only 與 draft PR 跳過 |
| `range.yml` | 在 GitHub runner 上用 OVS ＋ netns ＋ nftables 建六區，驗五條契約 |
| `pr-contract.yml` | 驗 PR base 分支，且必須引用**恰好一張** canonical issue |

### PR workflow

Issue 在 GitHub Issues（`Graylee0128/cyber`，`gh` CLI）。一張 issue ＝ 一個 canonical work
package ＝ 一個主要 draft PR。PR body 第一行寫 `Implements #N` / `Fixes #N`——**敘述句過不了
`pr-contract.yml` 的檢查**。

同一 branch / PR 不得混入另一個 canonical scope。

Triage 標籤：`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`。
`ready-for-agent` 只表示規格完整，真正可開工還要確認 issue body 的 Authoritative blockers
全部解除。

## 16. ADR / Specs / Known Limitations

### ADR

| ADR | 內容 |
|---|---|
| [0001 P1 Output Contract](../adr/0001-p1-output-contract.md) | Falco 是 sensor 不是 alert engine、`evidence_ref` 解析路徑、technique 由誰標、MTTR 定義、指標更名。**最常被引用的一份** |
| [0002 WS5 Objective & Scoring](../adr/0002-ws5-objective-scoring.md) | `telemetry_signal` 形狀、藍隊分數 v1 範圍、專屬端點 vs 通用 `/api/actions`、hint 取最大不累加 |
| [0003 WS8 Range Core ＋ Admission](../adr/0003-ws8-range-core-admission.md) | Admission 向 Range Core 發布在籍玩家身分，及其信任邊界後果 |
| [0004 Roster Attribution via Trusted Gateway](../adr/0004-roster-attribution-via-trusted-gateway.md) | 以單一可信 gateway 代宣告來源 IP，取代信任 `X-Forwarded-For` |

### Specs

沒有獨立的 `specs/` 目錄。活契約在 `.scratch/<workstream>/spec.md`（WS1 game design、
WS2 scenario/target、WS3 blue ops、WS5 range core、WS7 boundary、WS8 event control），
定版且開始被票消費後才升到 `docs/`。

另有 [`docs/p1-output-contract.md`](../p1-output-contract.md)（P1 定版對外契約）與
[`docs/agents/`](../agents/)（issue tracker、triage labels、domain 約定）。

### Known Limitations

見 §13 的缺口表與 [`ui/README.md`](../../ui/README.md) 的已知缺口清單。

**未驗證的運行範圍**：volumetric DDoS、高速封包擷取、malware detonation、multi-host 部署、
超過 #78 驗證人數的 participant envelope。
