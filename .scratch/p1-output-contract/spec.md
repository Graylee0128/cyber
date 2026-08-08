# Spec — P1 對外契約

- **Status**: ready-for-agent
- **決策依據**：[ADR 0001](../../docs/adr/0001-p1-output-contract.md)
- **上位計畫**：[purple_platform_plan.md](../../purple_platform_plan.md) Step 0

P1 的對外交付**不是 Grafana dashboard**，是三份契約：

1. Core Event Schema —— 給 Cyber Range Core / Battleboard / Console
2. Source Registry —— 給 Evaluation Engine 判斷缺口種類
3. Evidence API —— 給 Console 下鑽

三份都定版後 Step 0 才算完成，P2 才能開工。

---

# 1. 資料流（定版）

```text
Application ─┐
Host        ─┼→ OTel/Alloy ─→ Loki / Prometheus ─→ Grafana Alerting
Falco       ─┘   (sensor)         (storage)          (single brain)
                                                            │ webhook
                                                            ▼
                                                    P1 Webhook Receiver   ← 鑄造 event_id
                                                       ├── P1 Alert Record  (Z-MGMT，保留遙測細節)
                                                       └── Core Event       (遊戲語意，無遙測細節)
                                                                │
                                    ┌───────────────────────────┼───────────────┐
                                    ▼                           ▼               ▼
                          Evaluation Engine            Cyber Range Core   Blue SOC Console
                            (Z-MGMT)                                        (Z-APP)
```

**Falco 是 sensor，不是 alert engine。** Falco 事件一律寫入 Loki，
由 Grafana rule 決定是否構成告警。例外須在 scenario 定義中明寫。

Grafana `eval interval` = **10s**。

---

# 2. 契約一：Core Event Schema

## 2.1 欄位

```json
{
  "event_id":     "evt-01J...",
  "exercise_id":  "ex-001",
  "scenario_id":  "sqli-01",
  "event_type":   "attack.detected",
  "lifecycle":    "firing",
  "severity":     "high",
  "source":       "grafana",
  "team":         "red",
  "technique":    "T1190",
  "target":       { "service": "vulnerable-app" },
  "observed_at":  "2026-08-08T14:30:00+08:00",
  "visibility":   "purple"
}
```

**不得出現的欄位**（ADR ④）：

- ❌ `evidence_ref` —— 證據指標不進 Core，Core 永遠不讀它
- ❌ 任何 `loki`／`promql`／`logql` 字樣
- ❌ raw payload、rule threshold、internal IP mapping

判準：**Core 不該帶自己永遠不讀的欄位。** 換掉 Loki 時本 schema 一個字都不動。

## 2.2 `lifecycle`（ADR ⑥）

| 值 | 何時產生 | 用途 |
|---|---|---|
| `firing` | Grafana alert 轉 Firing | MTTD 的終點 |
| `resolved` | Grafana alert 轉 Resolved | `containment duration` 的終點 |

`pending` **不產生 Core event** —— 那是 Grafana 內部狀態，不是遊戲語意，
放進 timeline 只會產出「快要偵測到了」這種沒有資訊量的行。

同一個告警的 `firing` 與 `resolved` **共用同一個 `event_id`**，以 `lifecycle` 區分。

## 2.3 `technique`（ADR ⑤）

- 來源：**Grafana alert rule 自帶的 `technique` label**
- 值域：**technique 白名單**（見 §5），rule 與紅隊註冊清單共用同一份
- 白名單外的值 → receiver 拒收並記錄，不得靜默通過

紅隊那一側的 `technique` 來自演練前註冊的動作清單，不由本流程產生。

## 2.4 `visibility`（ADR ⑪）

由 **P1 Webhook Receiver 依 `event_type` 對照表決定**，Grafana rule **不得覆寫**。

| `event_type` | `visibility` |
|---|---|
| `attack.detected` | `public` |
| `detection.hit` | `blue` |
| `detection.miss` | `purple` |
| `response.executed` | `blue` |
| `response.failed` | `purple` |
| `exercise.*` | `instructor` |

理由：若 rule 作者能自己標 visibility，等於讓寫規則的人決定紅隊看得到什麼。

## 2.5 `event_id` 鑄造

由 **P1 Webhook Receiver** 產生，順序不可顛倒：

```text
1. 收到 Grafana webhook
2. 鑄造 event_id
3. 寫入 P1 Alert Record（帶 event_id）
4. 發出 Core Event（帶同一個 event_id）
```

join key 只有一個產生來源，兩邊不會各自生 id。

---

# 3. 契約二：Source Registry

## 3.1 為什麼需要

Purple Console 的下鑽畫面必須分辨兩件事（plan §3.7）：

```text
❌  來源有部署，但沒有這筆事件   → 算進可見性缺口
—   來源未部署                  → 不算缺口，只標示範圍
```

把「沒裝」算成「漏了」會系統性冤枉藍隊。

## 3.2 產生方式（ADR ⑧）

**期望 ＋ 實際，兩份資料合併**：

```text
期望清單  ← scenario 定義（這個 scenario 應該有哪些來源）
實際狀態  ← 各來源的 heartbeat
```

不得單靠部署狀態自動推導 —— 掉線的來源會從推導結果裡消失，變成「未部署」，
**等於把設備故障洗成免責**。

## 3.3 形狀

```json
{
  "scenario_id": "sqli-01",
  "sources": [
    { "id": "application-log", "expected": true,  "last_heartbeat": "14:59:58", "state": "healthy" },
    { "id": "falco",           "expected": true,  "last_heartbeat": "14:52:03", "state": "stale"   },
    { "id": "loki",            "expected": true,  "last_heartbeat": "14:59:59", "state": "healthy" },
    { "id": "waf",             "expected": false, "last_heartbeat": null,       "state": "absent"  }
  ]
}
```

| `state` | 條件 | Console 顯示 |
|---|---|---|
| `healthy` | expected 且 heartbeat 在容忍窗內 | `✅` 或 `❌`（視有無事件） |
| `stale` | expected 但 heartbeat 逾時 | 該時窗的動作標 `unknown` |
| `absent` | not expected | `—` |

heartbeat 間隔與容忍窗：**30s 間隔 / 90s 容忍**。

## 3.4 `unknown` 判定（ADR ⑫）

由 Evaluation Engine 自動判定，不需人工標註：

```text
動作時間窗 ∩ 來源 stale 區間 ≠ ∅  →  該動作標 unknown
原因欄自動填：「<source> 於 <start>–<end> 無心跳」
```

`unknown` **不進 action coverage 分母**，另外顯示數量與原因。

---

# 4. 契約三：Evidence API

## 4.1 端點

```text
GET /evidence/{event_id}
```

由 Evaluation Engine（Z-MGMT）提供，Purple Console 與 Blue SOC Console（Z-APP）呼叫。

**防火牆政策不變** —— Z-APP 仍然只跟 Evaluation API 講話，
不新增 `APP → MGMT :3100`。（ADR ②）

## 4.2 Engine 的解析流程

```text
event_id
   ↓
查 P1 Alert Record  →  取得 Grafana rule name / 原始 query / 觸發值 / labels
   ↓
按 source + observed_at ± window 查 Loki 或 Prometheus
   ↓
依呼叫者身分套用 visibility 過濾
   ↓
回傳上下文窗（非單一 log 行）
```

回傳上下文而非精確單行是**刻意的**：分析師要看的是事件前後發生什麼。

## 4.3 P1 Alert Record

住 Z-MGMT，**只有 Engine 讀**。這是遙測細節唯一合法的存放處。

```json
{
  "event_id":      "evt-01J...",
  "grafana_rule":  "SQLInjectionBurst",
  "query":         "<原始 LogQL>",
  "threshold":     "> 4 req / 1m",
  "fired_values":  [ ... ],
  "labels":        { ... },
  "backend":       "loki"
}
```

`backend` 欄位只存在於這裡。換掉 Loki 時，改的是 Engine 的查詢實作與這份紀錄，
**Core Event Schema 不動**。

---

# 5. Technique 白名單

單一檔案，scenario 定義時固定。Grafana rule label 與紅隊動作註冊清單**共用同一份**。

```yaml
# 層級必須統一 —— 不可一邊寫 T1190、另一邊寫 T1190.001
techniques:
  - id: T1190
    name: Exploit Public-Facing Application
    tactic: initial-access
  - id: T1078
    name: Valid Accounts
    tactic: initial-access
  - id: T1110
    name: Brute Force
    tactic: credential-access
    note: 需與成功登入證據連結後，才可敘述成入侵路徑
  - id: T1059
    name: Command and Scripting Interpreter
    tactic: execution
```

`note` 欄位承載 plan §3.6 的判讀限制，Console 顯示 technique 時一併呈現。

---

# 6. raw log 保留（ADR ⑩）

| | |
|---|---|
| 開啟時段 | 演練開始前 **10 分鐘** ～ 結束後 **30 分鐘** |
| 時段外 | 不保留未觸發規則的原始 log |
| Report | 產出時把引用到的證據**快照進報告本身** |

快照是必要的：retention 過期後，報告仍必須可讀。

---

# 7. 驗收

## 7.1 Schema

- [ ] Core Event 不含 `evidence_ref`，全文無 `loki`／`logql`／`promql` 字樣
- [ ] `firing` 與 `resolved` 共用 `event_id`，`pending` 不產生 event
- [ ] 白名單外的 `technique` 被 receiver 拒收並記錄
- [ ] Grafana rule 嘗試覆寫 `visibility` 時無效
- [ ] `event_id` 由 receiver 鑄造，Alert Record 先於 Core Event 寫入

## 7.2 Source Registry

- [ ] `expected` 來自 scenario 定義，非部署推導
- [ ] 來源掉線後 `state` 轉 `stale`，**不會**變成 `absent`
- [ ] Console 能分別渲染 `✅` / `❌` / `—`
- [ ] Engine 依 stale 區間自動標 `unknown` 並填入原因

## 7.3 Evidence API

- [ ] Console 可下鑽取得上下文
- [ ] `APP → MGMT` 防火牆規則**未新增**（實測 Console 直連 Loki `:3100` 應失敗）
- [ ] 回傳內容依呼叫者 visibility 過濾
- [ ] Report 快照後，模擬 retention 過期仍可讀

## 7.4 Falco 定位

- [ ] Falco 事件寫入 Loki，**未**直送 Event Service
- [ ] Falco 觸發但無對應 Grafana rule 時，Console 顯示為**偵測缺口**（非可見性缺口）
- [ ] Grafana `eval interval` = 10s

---

# 8. 已知未解

| 問題 | 影響 | 歸屬 |
|---|---|---|
| Grafana Alerting 是單點，掛了偵測全停 | 可用性 | 接受（ADR §5.3 代價 1），日後可加健康告警 |
| 「Falco 根本沒寫那條 rule」仍會呈現為可見性缺口 | 缺口分類的殘餘誤差 | 靠 rule inventory 補，不在本 spec |
| 紅隊動作註冊的 UI 形狀 | 分母能否演練前固定 | Product UI |
