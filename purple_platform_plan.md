# Purple Platform 工作規劃 — P1 / P2

- **文件版本**：v0.1
- **上位文件**：[資安攻防平台_系統架構設計文件_v0.1.md](./資安攻防平台_系統架構設計文件_v0.1.md) §4、§6
- **拓樸依據**：[demo_network_topology_v0_3.svg](./demo_network_topology_v0_3.svg)
- **架構依據**：[cyber_range_platform_layered_architecture.svg](./cyber_range_platform_layered_architecture.svg)
- **UI 構想來源**：[discuss.md](./discuss.md)（見文末附錄）
- **契約決策**：[ADR 0001](./docs/adr/0001-p1-output-contract.md) ／ 規格 `cyber/docs/p1-output-contract.md`

---

# 〇、本文件在產品中的位置

本文件談的是 **WS4 · Purple Platform** 一個工作包。它在八個 workstream
（SA §4）中的上下游如下 —— 完整依賴圖與約束見 **SA §4.1**：

```text
WS6 Range Infrastructure（環境、隔離、Reset）
        │  提供執行環境
        ▼
WS4-P1 Telemetry & Detection ──── 事實生產（本文件 §二）
        │  Core Event ＋ Alert Record（schema 已定版）
        ├──────────────► WS4-P2 Evaluation & Console ── 判讀（本文件 §三）
        ├──────────────► WS5 Cyber Range Core（Event／Score／Exercise State）
        └──────────────► WS3 Blue Operations（Incident／Response Workflow）
```

| 關係 | 對象 | 介面 |
|---|---|---|
| 上游依賴 | WS6 | 六區網段、五條跨世代契約、collector 落點 |
| 下游消費者 | WS5、WS3、WS4-P2 | **Core Event Schema**（§2.2：P1 唯一對外產出是 schema，不是面板）|
| 平行不相依 | WS1、WS7 | P2 的數字先存在，WS7 才有東西可畫（SA §4.1）|

**P2 不必等產品線（WS1 → WS5 → WS7）。** 它的輸入是 P1 已定版的 schema，
與遊戲規則正交，可與產品線平行推進。這也是 §1.1 說「Evaluation 是紫隊唯一獨有產出」
的實務意義：它不靠別人先定義完遊戲才能開工。

---

# 一、為什麼這樣切

## 1.1 不用「MGMT ／ UI」切

用位置切會漏掉一個東西：**Evaluation 無家可歸**。

- MGMT 那半是 infra 技能（部署 Alloy／Loki／Prom／Falco、寫 LogQL／PromQL、調誤報）
- UI 那半是前端技能（React、資料視覺化）

Evaluation（涵蓋率公式、證據分級、漏檢分類）兩種技能都不是，它是**領域邏輯**。
沒有指定歸屬時，它會漂向當下比較有空的那一側——而且歷史上都漂向前端。
本專案的前一版架構圖就已經漂過去了：Coverage／MTTD／MTTR 被畫在 Product UI 層。

Evaluation 是紫隊**唯一獨有**的產出。收 log 藍隊也能收，畫 dashboard 前端也能畫，
只有「判讀這個攻擊動作有沒有被記錄、有沒有規則命中、缺口是哪一種」是紫隊的。
切法不能讓它掉在縫裡。

## 1.2 改用「事實生產 ／ 判讀」切

| | 名稱 | 一句話 | 產出 |
|---|---|---|---|
| **P1** | Telemetry & Detection Pipeline | 把發生過的事變成可查詢的事實 | 標準化事件流 |
| **P2** | Evaluation & Console | 判讀這些事實，算出藍隊的偵測能力 | coverage／MTTD／MTTR／缺口分類 |

P2 **跨兩個網段**（Engine 在 Z-MGMT、Console 在 Z-APP）。這不是缺點——
中間那條就是純 API，反而逼介面定清楚，而且算分邏輯與顯示分數的留在同一個負責人手上，
數字的定義不會在引擎與畫面之間漂移。

---

# 二、P1 — Telemetry & Detection Pipeline

## 2.1 範圍

| 元件 | 內容 | 落點 |
|---|---|---|
| Collection | OpenTelemetry SDK／Grafana Alloy | **Z-TARGET**（契約 4） |
| Runtime sensor | Falco（syscall、process、container、敏感檔存取、shell spawn） | **Z-TARGET** |
| Ingest & Storage | Prometheus `:9090`、Loki `:3100`、OTLP `:4317` | Z-MGMT |
| Detection | Grafana Alerting（PromQL／LogQL）—— **唯一 alert engine**，Falco 不算 | Z-MGMT |
| Response | webhook → Response Orchestrator → agent pull → ipset／iptables | 派送在 Z-APP，執行在 Z-TARGET |

不含：Evaluation、Purple Console、Battleboard、Score。

## 2.2 唯一對外產出是 schema，不是 Grafana 面板

P1 的消費者有三個，只有一個是 UI：

```text
P1 標準化事件流 ──┬─→ P2 Evaluation Engine
                  ├─→ Blue SOC Console（藍隊在 Z-APP，不給 Grafana 底層存取）
                  └─→ Cyber Range Core / Event Service
```

三個消費者、只有一個 UI，所以 P1 的交付物必須定義成 **event schema**。
若 P1 把「做好 Grafana dashboard」當成交付，另外兩個消費者就拿不到東西。

## 2.3 收到不等於可用

每個來源都要確認欄位被正確拆出來。最低要求四項可被查詢與聚合：

```text
來源 IP · 目的 · 時間 · 動作結果
```

沒有這四項，紅隊動作與偵測事件就關聯不起來。

## 2.4 時間同步是前提，不是選配

所有來源必須對時。時間不同步，紅隊動作與藍隊偵測關聯不起來，
涵蓋率與延遲數字全部失去意義。這是 P2 全部計算的前提。

## 2.5 P1 要交的是兩份清單，不是一份

除了事件 schema，P1 還要維護一份**已註冊來源清單**（source registry）：

```text
id                expected   last_heartbeat   state
application-log   true       14:59:58         healthy
falco             true       14:52:03         stale     ← 掉線，不是「沒裝」
loki              true       14:59:59         healthy
waf               false      —                absent
```

理由在 P2 §3.7：沒有這份清單，Console 分不出「來源有裝但沒收到」與「來源根本沒裝」，
會把不在範圍內的東西算成藍隊漏檢。

**它必須由「期望 ＋ 實際」兩份資料合成**（[ADR 0001](./docs/adr/0001-p1-output-contract.md) ⑧）：

```text
expected        ← scenario 定義（這個 scenario 應該有哪些來源）
last_heartbeat  ← 各來源每 30s 上報，90s 未報轉 stale
```

**不可單靠部署狀態自動推導。** Falco 掛掉後會從推導結果裡消失，變成 `absent`／「未部署」
—— 那是把設備故障洗成不在範圍，正是 §3.5 `unknown` 要防的事。
`stale` 區間同時是 §3.5 判定 `unknown` 的依據與原因來源。

## 2.6 底層 CLI 驗證路徑不刪

`docker compose logs` ／ `curl /bans` ／ `ipset list` 這條路徑要留著，
不因為有了 dashboard 就移除。理由有兩個，都不是懷舊：

- **它是 dashboard 的對照組。** 數字對不上時要有辦法判斷是管路壞了還是呈現壞了。
- **它證明底層真的在動。** 一個只有畫面的 SOC demo 沒有說服力。

定位是分層，不是取捨：底層 CLI 是工程驗證層，Console 是呈現層，兩層都留。

## 2.7 P1 驗收

> 回填於 2026-08-10（大主機 `sudo bash test.sh` 四層全綠之後）。
> **只有拿得出實測證據的才打勾**；部分完成的一律維持未勾並註明缺哪一段 ——
> 「大致做完了」不是通過，把它勾起來會讓後面每一次狀態判斷都建立在假的基準上。

- [x] 六台 kali 的來源 IP 在事件中可分辨到個別攻擊者（macvlan 獨立 IP，非 SNAT）
      —— T3 實測 `10.167.30.11`～`.16` 六個可分辨。實作用 OVS veth + VLAN tag 而非
      macvlan，目標（不被 SNAT 塌縮）達成，機制不同，見 [#13](https://github.com/Graylee0128/cyber/issues/13) 結案留言
- [x] Alloy／Falco／response agent 全部部署在 Z-TARGET 側
      —— 三者均已烤進 golden 靶機；response agent 以 outbound pull 連指定
      `MGMT_RECEIVER_IP:8000`，不開 target inbound socket
- [x] `TARGET → MGMT` telemetry 三個 port 通，且反向不通（契約 2 的實測）
      —— T3：`:3100` 現場實證（打靶機 → Loki 60s 內收到新行）；`:9090`／`:4317` 由本顆 VM
      開機自驗且 boot nonce 相符。nftables 強制 telemetry 三埠 allowlist；response control
      只精準放行 `MGMT_RECEIVER_IP:8000`，其他 MGMT 主機的 `:8000` 與 `:22` 有反向斷言
- [x] `RED → MGMT` 實測 deny（契約 3 的實測）
- [ ] 每個來源的四項欄位驗證通過（§2.3：來源 IP · 目的 · 時間 · 動作結果）
      —— **無專屬驗證**。管線測試證明事件走得完，但沒有一條測試逐來源斷言那四項可查詢與聚合，
      見 [#29](https://github.com/Graylee0128/cyber/issues/29)
- [ ] 所有來源完成時間同步驗證
      —— `config/clock-nodes.yaml` 目前只有 `checker-host`。且不是取消註解就好：
      Falco／Alloy 現在是靶機 VM 內的 systemd unit 而非容器，`probe: docker` 探不到，
      見 [#29](https://github.com/Graylee0128/cyber/issues/29)
- [ ] SQLi 一條鏈跑通：`attack → log → alert → webhook → agent pull → ipset → 封鎖生效`
      —— 前四跳已在真環境實測；**後兩跳（agent pull → ipset）只到 unit 層**，由 [#44](https://github.com/Graylee0128/cyber/issues/44) 承接（原 #17 已吸收）
- [x] 事件 schema 定版並交付給三個消費者
      —— Core Event schema 定版（[ADR 0001](./docs/adr/0001-p1-output-contract.md)），
      `assert_core_event` 為契約守門；P2 由 canonical #21／#26／#28 承接
- [ ] source registry 由 **scenario 期望清單 ＋ 實際 heartbeat** 合成，P2 可查詢
      —— 原文寫「可由部署狀態自動產生」，與 §2.5 及 [ADR 0001](./docs/adr/0001-p1-output-contract.md) ⑧
      直接衝突（那裡明文禁止單靠部署狀態推導：Falco 掛掉會從推導結果消失、變成
      「未部署」，等於把設備故障洗成不在範圍）。2026-08-10 依 §2.5 修正措辭。
      現況：`evaluate_registry` 是純函式，全 repo 只有它自己與測試建構 `Registry`，
      兩份輸入都沒有生產路徑，見 [#18](https://github.com/Graylee0128/cyber/issues/18)

**現況：9 項中 5 項有實測證據，4 項未完成。** 未完成的都有票在追：
[#44](https://github.com/Graylee0128/cyber/issues/44)（第 7 項；原 #17 已吸收）、
[#18](https://github.com/Graylee0128/cyber/issues/18)（第 9 項）、
[#29](https://github.com/Graylee0128/cyber/issues/29)（第 5、6 項；原 #30 已吸收）。

---

# 三、P2 — Evaluation & Console

## 3.1 範圍

| 元件 | 內容 | 落點 |
|---|---|---|
| Evaluation Engine | 涵蓋率／確認率／MTTD／MTTR／漏檢分類 | **Z-MGMT** |
| Purple Console | 上述數字的呈現、ATT&CK 對應、缺口清單 | **Z-APP** |

不含 Battleboard——見 §3.7。

## 3.2 計分模型

不採用搶旗計分。

```text
executed actions  = hit(C1+) + miss + unknown
action coverage   = hit(C1+) / (hit + miss)
confirmation rate = C3 / (hit + miss)
```

**命名定案**（[ADR 0001](./docs/adr/0001-p1-output-contract.md) ⑨）：不使用 `Detection Rate`。
它沒有說分母是什麼，正是下面兩個陷阱的溫床；`action coverage` 的名字裡就寫著分母是「動作」。
SA §5.3 與 discuss.md 出現的 `Detection Rate` 一律視為歷史別名。

**延遲指標的兩個終點不可混用**（ADR ⑦）：

```text
MTTD                  → Grafana alert 轉 firing        （偵測到）
MTTR                  → Response action 生效           （處置完成）← 這才叫 Respond
containment duration  → Grafana alert 轉 resolved      （攻擊停了）
```

`containment duration` 測的是攻擊停了沒，**與藍隊做了什麼無關**，不得當成 MTTR 呈現。
MTTD 因 Grafana `eval interval` 有 ~10s 地板（ADR ③ 的已知代價），所有 scenario 一致，可比。

**兩個已知陷阱，兩者都必須實作，不是選配：**

**① 分母必須演練前固定。**
涵蓋率的分母來自紅隊上報。紅隊漏報一個動作，涵蓋率會**假性上升**；
上報了沒成功執行的動作，涵蓋率**假性下降**。
對策是演練前把劇本動作清單預先註冊，分母在開演前固定且公開。
現場的 feed 只是「開始」標記，不是分母的真相來源。

**② 涵蓋率必須與告警總量並列。**
只看涵蓋率會獎勵「對所有東西告警」。偵測到 8／10 手法但產生 4000 則告警，不是好的 SOC。
兩個數字並列，畫面才說得出真實的故事。

## 3.3 證據分級

| 等級 | 意義 |
|---|---|
| C1 | 攻擊嘗試被記錄（有這筆事件） |
| C2 | 入口被接受（攻擊真的到達目標，不只是被擋下的嘗試） |
| C3 | 跨來源確認（至少兩個獨立來源的事件互相印證） |

`hit` 門檻是 C1 以上；`confirmation rate` 只算 C3。

## 3.4 兩種漏檢必須分開

| 漏檢類型 | 意義 | 該補什麼 | 前提 |
|---|---|---|---|
| 沒有這筆 log | **可見性缺口** | 補收集來源 | — |
| 有 log 但沒規則 | **偵測缺口** | 補偵測規則 | 需保留 raw log |

這是紫隊最有教學價值的產出。混在一起講「藍隊沒偵測到」，會冤枉藍隊，也讓改善方向失焦。

**這條需求直接決定了 Evaluation Engine 的權限**——見 §四。

## 3.5 `unknown` 是合法狀態

來源掉線或資料不足導致無法判定，標記為 `unknown`，**不進涵蓋率分母**，另外顯示數量與原因。
偷算成 hit 是灌水，偷算成 miss 是把設備故障算在藍隊頭上。

## 3.6 ATT&CK 對應要寫判讀限制

每個紅隊動作對應到技法編號，但限制要寫清楚：
T1190、T1078 可屬 Initial Access；T1110 本身是 Credential Access，
**只有在與成功登入證據連起來後**，才能敘述成入侵路徑。

## 3.7 Purple Console 只有兩個畫面

SA §5.3 列了 11 項功能，但那是能力清單不是畫面。實際只需要兩個畫面，
第二個是第一個的下鑽。設計來源是 [discuss.md](./discuss.md) 的 Purple Analysis Mode。

### 畫面一：ATT&CK Coverage 表

| Technique | Red | Blue |
|---|---|---|
| T1190 Exploit Public-Facing App | ✅ | ✅ Detected |
| T1059 Command Interpreter | ✅ | ❌ Missed |
| T1087 Account Discovery | ✅ | ✅ Detected |
| T1071 Application Layer Protocol | ⏳ | — |

`⏳` 就是 §3.5 的 `unknown`，**不進分母**，不要另外發明一種狀態。
只列本 scenario 涉及的 technique（SA §10 scenario-based），不放完整 Enterprise Matrix。

### 畫面二：單一 technique 下鑽

點 `T1059` 進去：

```text
Attack
──────
Technique : T1059
Observed  : 14:31:04

Telemetry                    ← 這一欄就是漏檢分類
──────
Application Log   ✅
Falco             ❌
Loki              ✅
WAF               —  未部署

Detection
──────
Rule      : （無命中）
Latency   : —

Response
──────
（未觸發）

MTTD : —      MTTR : —
```

**這一欄不是裝飾，它就是 §3.4 的兩種漏檢畫出來的樣子**，判讀規則是機械的：

| Telemetry 欄 | Detection | 結論 |
|---|---|---|
| 全部 ❌ | 空 | **可見性缺口** — 補收集來源 |
| 至少一個 ✅ | 空 | **偵測缺口** — 補規則 |
| 至少一個 ✅ | 有命中 | hit，看 latency |

### 一個必須避開的坑：`❌` 與 `—` 不可混用

discuss.md 的範例把 `WAF` 列進 telemetry 欄，但 v0.2.1 根本沒有部署 WAF。
若來源清單寫死，沒部署的東西會顯示成 `❌`——那讀起來像藍隊漏了，實際上是不在範圍內。

所以來源欄必須**由已註冊的來源清單動態產生**，且兩種狀態分開：

```text
✅  有這筆事件
❌  來源有部署，但沒有這筆事件   → 算進可見性缺口
—   來源未部署                  → 不算缺口，只標示範圍
```

把「沒裝」算成「漏了」，會系統性地冤枉藍隊。

## 3.8 Exercise Report 是 P2 的收尾產出

即時數字之外，演練結束要能產出一份報告。這是紫隊唯一會被帶走的東西：

```text
Exercise Report
───────────────
Red    Attack Success 67%   ／ Objectives 4/7
Blue   action coverage 82%  ／ MTTD 12.4s ／ MTTR 14.2s ／ Alerts 4,120
Coverage gaps   T1059（偵測缺口）／ T1071（可見性缺口）
Unknown         2 項，原因：Falco 於 14:52–14:58 掉線
Recommended improvements ...
```

三個不可省的欄位，都是 §3.2／§3.4／§3.5 的直接後果：**告警總量**必須與 action coverage 並列；
coverage gap 必須標註是哪一種缺口；`unknown` 必須列出數量與原因。

## 3.9 Purple Console 不是 Battleboard

紫隊的 UI 只有 Purple Console 一個。**Battleboard 不屬於紫隊。**

理由：Battleboard 的 audience 包含 Red，而 SA §5.4 已列了不得公開的清單
（rule threshold、raw payload、detection query、Falco rule detail、ban TTL、internal IP mapping）。
紫隊自己做 Battleboard，等於紫隊在決定紅隊看得到什麼——那是 Product UI workstream 該扛的責任，
sanitization 邊界也該由第三方審核，不能自己審自己。

紫隊該交付的是 **sanitized event 的產生規則**，不是那塊畫面。

兩者的可讀性標準也不同，不要互抄：

| | audience | 標準 |
|---|---|---|
| Purple Console | 分析師、Instructor | 資訊密度優先，允許 raw drill-down |
| Battleboard | 全場、含非技術觀眾 | 5 公尺距離、3 位非技術觀眾、10 秒內能指出目前階段／被打的資產／有沒有被偵測到 |

### 公開畫面用「狀態」，不用「比率」

深淺分層是對的——Console 可以專業，Battleboard 要直觀。但「直觀」的正確做法是
**減少數字的數量**，不是**隱藏數字的前提**。

比率需要分母才成立，狀態不需要。所以：

| | 適合放哪 | 為什麼 |
|---|---|---|
| 攻擊鏈進度 ○／🟡／🔴／🟢 | **Battleboard** | 是狀態，不需要分母，一眼看懂 |
| `8 / 10 技法` | Battleboard 可 | 分數形式，分母是看得見的 |
| `action coverage 82%` | **只放 Console** | 裸百分比藏起分母，投影幕上的 `82%` 比表格裡的更難被質疑 |

投影幕會放大錯誤：算錯的數字做得越漂亮越危險，因為沒人會當場去問分母是什麼。
紫隊對 Battleboard 的義務只有一條——**不提供裸百分比給公開層**，換成狀態或分數形式。

## 3.10 P2 驗收

- [ ] 紅隊動作清單可在演練前註冊，分母開演前固定
- [ ] 涵蓋率、確認率、告警總量三個數字可自動計算
- [ ] 兩種漏檢可分開輸出（可見性缺口 vs 偵測缺口）
- [ ] `unknown` 不進分母，且數量與原因可查
- [ ] 每個動作可回溯到證據等級（C1／C2／C3）
- [ ] ATT&CK 對應含判讀限制欄位
- [ ] Coverage 表的 `⏳` 與 `unknown` 是同一件事，非另一種狀態
- [ ] 下鑽畫面的來源欄由註冊清單動態產生，`❌`／`—` 分開（§3.7）
- [ ] Exercise Report 可產出，且含告警總量、缺口分類、`unknown` 原因
- [ ] Console 對 Z-MGMT 只有 Evaluation API，無 raw query 權（實測）
- [ ] 完成 20 次延遲量測並記錄 p50／p95

---

# 四、P1 ↔ P2 介面

介面**兩條，不是一條**：

```text
① P1 事件流  ──────────→  P2 Evaluation Engine     （Z-MGMT 內，同區）
② Engine API ──────────→  P2 Purple Console        （Z-MGMT → Z-APP，跨區）
```

G3 六區拓樸新增 Z-EDGE（VLAN 50）與 Z-BLUE（VLAN 60），但 P2 的 Evaluation Engine
仍在 Z-MGMT、Purple Console 仍在 Z-APP；既有 coverage／MTTD／MTTR 與缺口分類語意不變。

## 4.1 Evaluation Engine 必須有 raw 查詢權

這一點要講清楚，因為直覺會想把 Engine 也擋在 schema 之外：

**做不到。** §3.4 的兩種漏檢，要分辨「沒有這筆 log」與「有 log 但沒規則」，
就必須能查**沒有觸發任何規則的原始 log**。只吃 alert 的話，兩種漏檢在資料上完全一樣，
分不出來——而那正是紫隊最有價值的產出。

所以權限線畫在 Console 那一層，不是 Engine：

| 元件 | 網段 | raw LogQL／PromQL | 說明 |
|---|---|---|---|
| Evaluation Engine | Z-MGMT | **有** | 與資料同區，漏檢分類的必要條件 |
| Purple Console | Z-APP | **無** | 只吃 Engine API，符合拓樸政策 `APP → MGMT: allow Evaluation API` |

## 4.2 raw log 保留是有成本的

保留未觸發規則的原始 log 明顯耗磁碟。建議**僅在受控時段開啟**（演練期間 + 前後緩衝），
不是常態全開。這是 P1 要提供的開關，P2 要負責在報告裡註明哪些時段有 raw 覆蓋。

---

# 五、階段與依賴順序

```text
Step 0  P1 對外契約定版                 ← ✅ 完成 2026-08-08
Step 1  P1 跑通 SQLi 一條鏈             ← SA §7 已有 prototype，先復現
Step 2  P1 補 Falco runtime 場景        ← 證明不只靠 application log
Step 3  P2 Engine 接上，算出第一個涵蓋率  ← 此時可先用 CLI／JSON 輸出，不做 UI
Step 4  P2 Console                     ← 最後做，數字已經對了才畫
Step 5  Response agent pull 閉環驗證     ← 需要 Z-APP 就位
```

**Step 0 是硬阻塞，現已解除。** 三份契約（Core Event Schema、Source Registry、Evidence API）
定版於 `cyber/docs/p1-output-contract.md`，決策理由見
[ADR 0001](./docs/adr/0001-p1-output-contract.md)。

事實證明 schema 不能由 P1 單方決定：本次十二項決策裡，**⑦ MTTR 終點、⑧ source registry
形狀、⑫ `unknown` 判定**三項全部是 P2 的需求回頭改寫 P1 的產出。

**Step 3 刻意不做 UI。** 先讓數字正確，再讓數字好看。
把被卡住的等待時間拿去蓋完整 UI 是本專案最容易犯的錯。

# 六、人力建議

SA §4 建議 Purple Platform 2 人。

| 人 | 前期 | 後期 |
|---|---|---|
| A | P1 全部（infra 重） | P1 維運 + Falco 場景擴充 |
| B | Step 0 schema + P2 Engine（後端重） | P2 Console（前端重） |

B 的角色前後期性質差很多，這是刻意的：**同一個人做引擎再做畫面，
數字的定義才不會在兩者之間走樣。**

---

# 七、開放問題

## 7.1 已解（2026-08-08，[ADR 0001](./docs/adr/0001-p1-output-contract.md)）

| # | 問題 | 結論 |
|---|---|---|
| Q1 | `evidence_ref: "loki://..."` 洩漏 | **Core event 不帶此欄位**。證據住 P1 Alert Record（Z-MGMT），兩份用 `event_id` 對接；Console 走 `GET /evidence/{event_id}` 由 Engine 代取，防火牆不動 |
| Q2 | Falco 直送 vs 經 Grafana | **一律經 Grafana**。Falco 定位改寫為 runtime sensor —— 直送會讓 Falco 覆蓋範圍內的偵測缺口不可觀測，直接破壞 §3.4 |
| Q3 | Grafana 是否唯一 alert engine | **是**（`eval interval` 10s）。代價：Grafana 成為單點；MTTD 有 ~10s 地板 |
| Q4 | raw log 保留策略 | **演練前 10 分 ～ 後 30 分**；Report 產出時把引用到的證據快照進報告 |
| Q8 | 指標命名 | **只留 `action coverage`**，`Detection Rate` 廢除為歷史別名 |
| — | MTTR 的終點（本次新發現） | **Response action 生效**，不是 Grafana Resolved。後者另名 `containment duration` |
| — | `technique` 誰標 | Grafana rule label 自帶；紅藍兩側共用一份 **technique 白名單**，層級必須統一 |
| — | `visibility` 誰決定 | P1 receiver 依 `event_type` 對照表，rule 不得覆寫 |
| — | source registry 產生方式 | **期望清單 ＋ heartbeat**，不可單靠部署推導（掉線會被洗成「未部署」） |
| — | `unknown` 誰判 | Engine 依 heartbeat 缺口自動判，原因欄自動填 |

實作規格：`cyber/docs/p1-output-contract.md`。

## 7.2 仍未解

| # | 問題 | 影響 | 誰決定 |
|---|---|---|---|
| Q5 | 紅隊動作清單的註冊介面長什麼樣 | §3.2 分母能否演練前固定 | Product UI（契約已定：必須從 technique 白名單選） |
| Q6 | 是否導入 Tempo（tracing） | 是否擴大 P1 範圍 | 暫緩至 V2 |
| Q7 | Purple 是否直接參與紅藍比分 | Purple 的產出是 KPI 還是分數 | 產品決定，非紫隊 |
| Q9 | Grafana Alerting 單點的可用性補償 | 它掛了偵測全停 | P1，接受風險後日後補健康告警 |

Step 0 的阻塞題已全部解除，P1 可開工。

---

# 附錄：來源文件

- [discuss.md](./discuss.md) — 最早的 dashboard 構想。絕大部分已被 SA v0.1 吸收
  （`visibility` enum → §8.4、Containment Rate → §9、scenario-based ATT&CK → §10、
  Game Event Service → §8）。未被吸收而在本文件補上的有兩項：Purple Analysis Mode
  的下鑽結構（→ §3.7）、Engineering Lab／Portfolio Demo 分層原則（→ §2.6）。
- discuss.md 的 KPI 面板**沒有分母紀律**（`Detection Rate 82%` 是裸百分比）。
  照抄會直接踩進 §3.2 的兩個陷阱，引用時必須帶上分母固定與告警總量並列兩條。
