# ADR 0001 — P1 對外契約：Falco 定位、evidence 解析路徑、事件標註責任

- **狀態**：Accepted
- **日期**：2026-08-08
- **決策者**：gray
- **產生方式**：grilling session（靶：`evidence_ref` 抽象層級 ＋ alert engine 是否單一）
- **影響範圍**：[SA v0.1](../../資安攻防平台_系統架構設計文件_v0.1.md) §6.1／§8.3／§8.4、[purple_platform_plan.md](../../purple_platform_plan.md) Q1–Q8
- **實作規格**：`cyber/docs/p1-output-contract.md`

---

# 1. 背景

SA v0.1 內部有兩處自相矛盾，兩處都卡在 P1 的對外契約上：

**矛盾一 —— schema 分離被自己違反。**
§3.3 明訂「Telemetry Schema 與 Game Event Schema 分離」，但 §8.4 的 event 草案裡寫著
`evidence_ref: "loki://..."`。Cyber Range Core 的 domain model 直接知道了 log backend 是 Loki。
換掉 Loki 就要改 Core schema，§3.3 想避免的正是這件事。

**矛盾二 —— Falco 同時是兩種東西。**
§6.1 把 `Falco Rules` 與 `Grafana Alerting` 並列在 Detection 之下；
§8.3 又把 `Falco` 與 `Grafana Webhook` 並列為 Event Service 的直接輸入來源；
§7 的 Scenario 02／03 寫死 `Falco → Event → Response`。
也就是：**SA 已經默默選了「不只一個 alert engine」，但沒有承認，也沒寫代價。**

這兩件事都必須在 P1 開工前定案 —— 它們決定事件長什麼樣，而事件 schema 是 P1 的唯一對外交付。

---

# 2. 決策總表

| # | 決策 | 選定 | 被否決 |
|---|---|---|---|
| ① | `evidence_ref` 的消費者 | **C** 留著且要能被點開 | A 拿掉／B 僅內部 |
| ② | raw 如何從 Z-MGMT 到 Z-APP | **C2** Evaluation API 代取 | C1 直連 Loki／C3 事件內嵌 |
| ③ | Falco 走哪條路 | **D1** Falco→Loki→Grafana→Event | D2 直送／D3 雙目的地 |
| ④ | `evidence_ref` 抽象層級 | **E3** Core event 不帶此欄位 | E1 `loki://`／E2 中性座標／E4 不透明 token |
| ⑤ | 誰標 `technique` | **F1** Grafana rule 自帶 label ＋ technique 白名單 | Core 對照表／receiver 查表 |
| ⑥ | 一個告警產生幾個 event | **G2** Firing ＋ Resolved | G1 只有 Firing／G3 含 Pending |
| ⑦ | MTTR 的終點 | **H2** Response action 生效 | H1 Grafana Resolved／H3 人工關閉 |
| ⑧ | source registry 產生方式 | **I3** 期望清單 ＋ heartbeat | I1 手寫／I2 自動推導 |
| ⑨ | 指標命名 | **只留 `action coverage`** | `Detection Rate` 降為歷史別名 |
| ⑩ | raw log 保留策略 | **J2** 演練前 10 分 ～ 後 30 分 ＋ 報告快照 | J1 常態全開／J3 不留 |
| ⑪ | `visibility` 由誰決定 | **K2** P1 receiver 依 `event_type` 對照表 | K1 rule 作者／K3 Core |
| ⑫ | `unknown` 判定 | **L1** Engine 依 heartbeat 缺口自動判 | L2 人工標註 |

---

# 3. 逐項 trade-off

## ① `evidence_ref` 的消費者是誰

起手是先問「它該不該存在」，而不是「怎麼抽象化」。

| 候選消費者 | 判斷 |
|---|---|
| Purple Console | 在 Z-APP，plan §4.1 定了無 raw query 權 → 拿到 `loki://` 是**死連結** |
| Blue SOC Console | 同上 |
| Battleboard | §5.4 sanitization 明文禁止 |
| Evaluation Engine | 在 Z-MGMT 有 raw 權，但可用 `source + observed_at` 自己查 |

**初判是「拿掉」（選項 A），這個初判有錯。**
錯在把權限現況當成需求結論：藍隊調查 incident 不看 raw payload 根本做不了事，
「Console 現在沒有 raw 權」是設計的結果，不是需求不存在的證據。

**選 C —— 留著，而且要能被點開。**

放棄的：「零查詢路徑」的簡潔性。多了一個 endpoint 與一份對照表要維護。
換到的：藍紫兩隊的調查能力，這是 SOC 訓練平台的核心功能，不能因為架構好看而砍掉。

## ② raw 如何從 Z-MGMT 到 Z-APP

C 一旦成立，就得開一條路。現行拓樸只有 `APP → MGMT: allow Evaluation API` 一條。

| | 做法 | 防火牆 | sanitization 位置 | 代價 |
|---|---|---|---|---|
| C1 | Console 直連 Loki `:3100` | **新增規則** | 分散在各前端 | 破壞政策；紫隊看到的與藍隊看到的會慢慢漂移 |
| **C2** | Evaluation API 加 `GET /evidence/{event_id}` | **不變** | Engine 單一收斂點 | 多寫一個 endpoint |
| C3 | 事件產生時內嵌 raw 片段 | 不變 | 寫入時 | Core DB 存了 payload，而 §8.3 的輸出端**包含 Battleboard** |

**選 C2。** 判準是 sanitization 的收斂點：C1 讓每個前端自己決定顯示什麼，
兩個 Console 的顯示規則遲早分岔；C2 把「誰能看到什麼」壓在一個地方，
而 `visibility` 欄位本來就是為此存在。

**C3 的危險要點名**：Core event 一旦內嵌 raw payload，公開的 Battleboard 與原始 payload
之間就只隔一個欄位過濾器 —— 這是最容易在某次改版被誤放出去的形狀。

但 C3 有**一個成立的場景**：Exercise Report。報告必須在 Loki retention 過期後仍可讀，
所以報告產出時要把引用到的證據快照進報告本身。這與即時下鑽是兩件事，見 ⑩。

放棄的：一次查詢的延遲（多一跳）。可接受。

## ③ Falco 走哪條路 —— 本次最重要的一題

**先確立一個砍選項的結構性事實**：Falco 的偵測發生在 syscall 層，Grafana 看不到 syscall。
所以「單一 alert engine」不是選擇題，**結構上就做不到**。真正待決的是下一層：
Falco 吐出來之後，要不要再經過 Grafana。

| | 路徑 | MTTD | 得到 | 失去 |
|---|---|---|---|---|
| **D1** | Falco→Loki→Grafana rule→webhook→Event | ~10–15s | 統一 lifecycle、threshold、dedup、單一 rule 清單 | 延遲地板 |
| D2 | Falco→Event 直送 | ~1s | 低延遲、簡單 | 無 threshold、無 lifecycle、Loki 沒有留存 |
| D3 | Falco→Loki **＋** Falco→Event | ~1s | 低延遲又有留存 | 兩個 alert engine |

### 決定性的一點：D3 讓 Falco 的偵測缺口不可觀測

plan §3.4 的核心產出是分辨「沒有 log」與「有 log 但沒規則」。

D3 底下 Falco 一觸發就**直接變成告警** —— 系統裡不存在「telemetry 有、detection 沒有」
這個中間狀態，Falco 的事件同時是證據又是告警。於是 Falco 覆蓋範圍內的偵測缺口全部不可觀測。

D1 保留了那個中間狀態：

```text
Falco rule 觸發 → Loki（telemetry ✅）→ Grafana rule 判斷
                                          ├─ 有規則 → hit
                                          └─ 無規則 → 偵測缺口 ✅ 看得見
```

**誠實補一句**：兩者都解不了「Falco 根本沒寫那條 rule」的情形 —— 那時 Loki 也是空的，
看起來會像可見性缺口。那個要靠 source registry ＋ rule inventory 補（見 ⑧），不是靠路由解決。
但 D1 至少救回其中一種，D3 一種都不救。

### 一個被推翻的反對意見

grilling 過程中我先反對 D1，理由是「Falco 判過一次，Grafana 再判一次，是重複偵測」。
那個框架不對，兩者判的不是同一件事：

```text
Falco    ：shell 被 spawn 了              ← 事實（sensor）
Grafana  ：1 分鐘內 3 次，構成告警          ← 判斷（alerting）
```

這是乾淨的分工。而且它讓「偵測規則寫在哪」只有一個答案 ——
D3 需要額外維護的跨兩邊 rule inventory，在 D1 直接不存在。

### 延遲的代價實際有多大

演練長 30–45 分鐘，真實世界 SOC 的 MTTD 以小時計。投影幕上「12 秒偵測到」與
「1 秒偵測到」的教學效果一樣強。**用永久的結構代價換一個對產品沒有意義的 9 秒，不划算。**

而 D3 的代價是結構性且永久的：無 Resolved 語意（MTTR 算不出來）、無 dedup
（一次 shell spawn 可能 20 個事件，Battleboard timeline 不能看）、
跨 scenario 的 MTTD 不可比、偵測邏輯住兩個地方。

**選 D1。Falco 的定位從 alert engine 改寫為 runtime sensor。**
Grafana eval interval 設 10s。

**留一個例外閘門**：若日後某 scenario 真的需要次秒級自動圍堵，可申請走直送，
但必須在 scenario 定義裡明寫。是例外，不是預設。

放棄的：次秒級偵測延遲。承擔的：Grafana Alerting 成為單點 —— 它掛了偵測就全停。

## ④ `evidence_ref` 的抽象層級

D1 定案後只剩一條路徑，這題大幅簡化。

**關鍵事實**：SA §8.4 的 event **已經有** `source`、`observed_at`、`target`、`scenario_id`、
`exercise_id`。這些本身就是中性座標，足以定位一段時間窗 ＋ 一個來源。
再加一組中性欄位是疊床架屋。

| | 形狀 | Core 知道 Loki？ | 問題 |
|---|---|---|---|
| E1 | `"loki://{query}"` | **知道** | 違反 §3.3 |
| E2 | `{source, stream, time_range}` | 半知道 | `stream` 是 Loki 概念；且 `source`＋`observed_at` 已存在 |
| **E3** | **不帶此欄位** | 不知道 | 拿到的是上下文而非精確單行 |
| E4 | 不透明 token | 不知道 | Core 存了一個自己永遠不讀的欄位 |

**選 E3。判準是一條規則：domain model 不該帶自己永遠不讀的欄位。**
Core 會讀 `technique`、`visibility`、`team`、`event_id`；它永遠不會讀 `evidence_ref`。

### 這不推翻 ① 選的 C

Console 照樣點得開，只是指標不住在 Core。承認**有兩份紀錄，不是一份**：

| | 住哪 | 內容 | 誰讀 |
|---|---|---|---|
| Core event | Cyber Range Core | 遊戲語意：technique、team、visibility | Battleboard／Score／Console |
| **P1 alert record** | Z-MGMT，P1 擁有 | 遙測語意：Grafana rule、LogQL、觸發值、原始 label | 只有 Engine |

```text
Console 點 event ──→ GET /evidence/{event_id} ──→ Engine
                                                    ├─ 查 P1 alert record
                                                    └─ 按 source + observed_at ± window 取回上下文
```

兩份用 `event_id` 對接。**`event_id` 由 P1 的 webhook receiver 鑄造** ——
先建 alert record，再帶同一個 id 發出 Core event。join key 只有一個來源。

換掉 Loki 時要改的只有 Engine 的查詢實作，Core schema 一個字都不動。

放棄的：精確定位到單一 log 行。這其實是優點 —— 分析師要看的是事件前後發生什麼。

## ⑤ 誰標 `technique`

§3.7 的 coverage 表有 Red 與 Blue 兩欄，**來源不同**：

| 欄 | 來源 |
|---|---|
| Blue（偵測側） | **Grafana alert label 自帶 `technique`** |
| Red（攻擊側） | 演練前註冊的動作清單（唯一來源，無選項） |

**選 F1。** 寫規則的人最清楚這條規則抓什麼，而 alert label 是 Grafana 原生機制。

**真正的風險不在誰標，在兩邊用不用同一套詞彙。**
紅隊註冊寫 `T1190`、規則 label 寫 `T1190.001`，coverage 表永遠對不起來。
因此新增一份 **technique 白名單**，兩邊都只能從裡面選，scenario 定義時固定。

放棄的：sub-technique 的精細度（白名單層級要統一）。

## ⑥ 一個告警產生幾個 Core event

| | 產生 | 後果 |
|---|---|---|
| G1 | 只有 Firing | **MTTR 算不出來** |
| **G2** | **Firing ＋ Resolved** | MTTD 用 Firing、MTTR 用 Resolved，剛好夠 |
| G3 | 三個都產生 | Battleboard timeline 出現「快要偵測到了」這種沒有資訊量的行 |

**選 G2。** Pending 是 Grafana 的內部狀態，不是遊戲語意。

## ⑦ MTTR 的終點

有了 lifecycle 之後這題才浮現，而且**原本的直覺定義是錯的**：

| | 終點 | 實際在測什麼 |
|---|---|---|
| H1 | Grafana Resolved | 攻擊停了沒 —— **與藍隊做了什麼無關** |
| **H2** | **Response action 生效（ipset 寫入成功）** | 處置速度 |
| H3 | 藍隊手動關閉 incident | 混入人工作業時間，噪音大 |

**選 H2。** 名字就叫 Mean Time To **Respond**，終點該是處置生效。

H1 那個數字仍然有用，但**必須另外命名為 `containment duration`**，不得叫 MTTR。

## ⑧ source registry 的產生方式

| | 做法 | 問題 |
|---|---|---|
| I1 | 手寫 YAML | 會與實際部署漂移 |
| I2 | 從部署狀態自動推導 | **掉線的來源會消失** → 被判成「未部署」 |
| **I3** | **期望清單（scenario 定義）＋ 來源 heartbeat** | 要多做 heartbeat |

**選 I3。** plan §3.7 要分辨 `❌ 有裝沒收到` 與 `— 未部署`，這需要**期望 vs 實際兩份資料**。

I2 單獨用不成立：Falco 掛掉後從自動推導裡消失，就變成「不在範圍內」——
**那是把設備故障洗成免責**，正是 §3.5 `unknown` 要防的事。

放棄的：實作簡便。承擔的：每個來源要送 heartbeat。

## ⑨ 指標命名

**只留 `action coverage`，`Detection Rate` 降為歷史別名，文件與畫面一律不再使用。**

`Detection Rate` 沒有說分母是什麼，這正是 plan §3.2 兩個陷阱的溫床；
`action coverage` 的名字裡就寫著分母是「動作」。

SA §5.3 與 discuss.md 都用了 `Detection Rate`，兩處標記為別名即可，不需回頭重寫歷史文件。

## ⑩ raw log 保留策略

| | | |
|---|---|---|
| J1 | 常態全開 | 磁碟吃不消 |
| **J2** | **演練前 10 分鐘 ～ 結束後 30 分鐘** | 夠用 |
| J3 | 不留 raw | **直接殺死 §3.4**，不可選 |

**選 J2**，並加一條：**Exercise Report 產出時把引用到的證據快照進報告**，
使 retention 過期後報告仍可讀。這是 ② 裡 C3 唯一成立的場景。

放棄的：演練時段外的事後追查能力。

## ⑪ `visibility` 由誰決定

| | | |
|---|---|---|
| K1 | Grafana rule 作者自己標 | **等於讓寫規則的人決定紅隊看得到什麼** |
| **K2** | **P1 receiver 依 `event_type` 對照表決定，rule 不可覆寫** | 收斂在一處 |
| K3 | Core 決定 | Core 不該知道遙測細節 |

**選 K2**，對照表與 `event_id` 鑄造在同一處，沿用 ② 的 sanitization 單一收斂點原則。

## ⑫ `unknown` 判定

**選 L1：Engine 依 source registry 的 heartbeat 缺口自動判。**

某動作的時間窗內相關來源無 heartbeat → 標 `unknown`，原因欄自動填
`<source> 於 14:52–14:58 無心跳`。§3.5 要求列出原因，heartbeat gap 天然就是原因。

放棄的：人工覆寫的彈性（可日後再加，但預設自動）。

---

# 4. 決策之間的依賴

不是十二個獨立決定，是一條鏈：

```text
①  evidence 要能被點開
     │
     ├─→ ②  必須開一條路 → C2 代取（因為不願為省一個 endpoint 開防火牆）
     │        └─→ ⑪  sanitization 收斂點確立 → visibility 歸 P1 receiver
     │
     └─→ ④  指標住哪 → E3（因為 ③ 定案後只剩一條路徑，形狀單純）

③  Falco 是 sensor 不是 alert engine
     ├─→ ④  只有一種 evidence 形狀
     ├─→ ⑤  technique 從 Grafana rule label 來（只有一個標註點）
     ├─→ ⑥  有了 lifecycle 才能產生 Resolved
     │        └─→ ⑦  MTTR 的終點才成為問題 → H2
     └─→ ⑧  rule inventory 只有一份（D3 才需要兩份）

⑧  source registry 有「期望 vs 實際」
     └─→ ⑫  heartbeat 缺口即 unknown 的判定依據與原因
```

**⑦ 是被 ③ 誘發的**：D3 底下沒有 Resolved，MTTR 根本算不出來，這題不會浮現。
選了 D1 才發現原本對 MTTR 的定義是錯的。

---

# 5. 後果

## 5.1 要改的既有文件

| 檔案 | 改什麼 |
|---|---|
| SA §6.1 | `Falco Rules` 從 Detection 移到 Collection；Falco 定位改寫為 runtime sensor |
| SA §8.3 | Event Service 輸入來源移除 `Falco`（改為經 Grafana Webhook） |
| SA §8.4 | 移除 `evidence_ref`；新增 `event_type` 的 lifecycle 語意 |
| SA §5.3 | `Detection Rate` 標記為 `action coverage` 的歷史別名 |
| plan | Q1／Q2／Q3／Q4／Q8 標為已解；§3.9 MTTR 定義；§2.5 source registry 補 heartbeat |

## 5.2 新增的東西

- `GET /evidence/{event_id}` —— Evaluation API 的證據代取 endpoint
- **P1 alert record** —— Z-MGMT 側的遙測紀錄，與 Core event 用 `event_id` 對接
- **technique 白名單** —— 紅隊註冊與 Grafana rule label 共用
- **source heartbeat** —— 每個來源定期上報
- **`event_type` → `visibility` 對照表** —— 在 P1 receiver

## 5.3 承擔的代價（明寫，不裝作沒有）

1. **Grafana Alerting 成為單點。** 它掛了，偵測全停。D1 的直接後果。
2. **MTTD 有 ~10s 地板。** 換來跨 scenario 可比性與 lifecycle。
3. **每個來源要實作 heartbeat。** 換來漏檢分類的正確性。
4. **演練時段外沒有 raw log。** 事後追查只能靠報告快照。
5. **多一個 endpoint ＋ 一份 alert record 儲存。** 換來防火牆政策不動。

---

# 6. 過程中被推翻的判斷

留這一節是為了讓後續 session 不必重走一遍。

| 我先前主張 | 為什麼錯 |
|---|---|
| 「Blue SOC Console 不需要 raw」 | 把權限現況當成需求結論。藍隊調查 incident 必須看 raw payload |
| 「推薦 D3」 | 低估了告警 lifecycle（沒有 Resolved 就沒有 MTTR）與 D3 對 §3.4 的破壞 |
| 「D1 是重複偵測」 | 框架錯。Falco 判事實、Grafana 判是否構成告警，是分工不是重複 |
| 「evidence_ref 該拿掉（選項 A）」 | 同第一項。正確答案是留著但換位置（E3），不是刪掉 |

---

# 7. 尚未決定（不在本 ADR 範圍）

| 問題 | 歸屬 |
|---|---|
| 紅隊動作註冊的 UI 形狀 | Product UI（契約已由 ⑤ 定：必須從 technique 白名單選） |
| Purple 是否直接參與紅藍比分 | 產品決定 |
| 是否導入 Tempo（tracing） | 暫緩至 V2 |
