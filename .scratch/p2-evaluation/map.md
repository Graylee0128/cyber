# P2 Evaluation & Console — 執行導覽

執行進行中的地圖，做完後比照 P1 遷入 `archive/`（見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）。
票在 GitHub Issues（#21–#28），契約散在票身裡；這份地圖不重複票的內容，只做兩件事：
把票裡的相對引用接到真實錨點，把跨票依賴畫成一張圖。

## 錨點對照表

票身裡的 `§x.y` 都是相對引用，指向 [purple_platform_plan.md](../../purple_platform_plan.md)
或 SA（[資安攻防平台_系統架構設計文件_v0.1.md](../../資安攻防平台_系統架構設計文件_v0.1.md)）。
下表把每個在 #21–#28 裡出現過的引用接到真實錨點（GitHub heading slug：句號與 `&` 直接移除不留空格、空白轉連字號、大小寫轉小寫）。

| 票內引用 | 出處 | 標題 | 錨點 |
|---|---|---|---|
| §3.2 | #21 #22 #24 #25 | 3.2 計分模型 | [purple_platform_plan.md#32-計分模型](../../purple_platform_plan.md#32-計分模型) |
| §3.3 | #23 | 3.3 證據分級 | [purple_platform_plan.md#33-證據分級](../../purple_platform_plan.md#33-證據分級) |
| §3.4 | #22 #27 #28 | 3.4 兩種漏檢必須分開 | [purple_platform_plan.md#34-兩種漏檢必須分開](../../purple_platform_plan.md#34-兩種漏檢必須分開) |
| §3.5 | #22 #26 #28 | 3.5 `unknown` 是合法狀態 | [purple_platform_plan.md#35-unknown-是合法狀態](../../purple_platform_plan.md#35-unknown-是合法狀態) |
| §3.6 | #23（technique 判讀限制） | 3.6 ATT&CK 對應要寫判讀限制 | [purple_platform_plan.md#36-attck-對應要寫判讀限制](../../purple_platform_plan.md#36-attck-對應要寫判讀限制) |
| §3.7 | #26 #27 | 3.7 Purple Console 只有兩個畫面 | [purple_platform_plan.md#37-purple-console-只有兩個畫面](../../purple_platform_plan.md#37-purple-console-只有兩個畫面) |
| §3.7 畫面一 | #26 | 畫面一：ATT&CK Coverage 表 | [purple_platform_plan.md#畫面一attck-coverage-表](../../purple_platform_plan.md#畫面一attck-coverage-表) |
| §3.7 畫面二 | #27 | 畫面二：單一 technique 下鑽 | [purple_platform_plan.md#畫面二單一-technique-下鑽](../../purple_platform_plan.md#畫面二單一-technique-下鑽) |
| §3.8 | #28 | 3.8 Exercise Report 是 P2 的收尾產出 | [purple_platform_plan.md#38-exercise-report-是-p2-的收尾產出](../../purple_platform_plan.md#38-exercise-report-是-p2-的收尾產出) |
| §3.10 | #25（第 11 條）#26（第 10 條） | 3.10 P2 驗收 | [purple_platform_plan.md#310-p2-驗收](../../purple_platform_plan.md#310-p2-驗收) |
| §4.1 | #26（Console 只有 Evaluation API） | 4.1 Evaluation Engine 必須有 raw 查詢權 | [purple_platform_plan.md#41-evaluation-engine-必須有-raw-查詢權](../../purple_platform_plan.md#41-evaluation-engine-必須有-raw-查詢權) |
| §4.2 | #28（retention 過期的報告顯示） | 4.2 raw log 保留是有成本的 | [purple_platform_plan.md#42-raw-log-保留是有成本的](../../purple_platform_plan.md#42-raw-log-保留是有成本的) |
| ADR ⑦ | #25 | MTTR 終點＝response 生效 | [docs/adr/0001-p1-output-contract.md](../../docs/adr/0001-p1-output-contract.md)（決策⑦，條列項無獨立錨點） |
| ADR ⑨ | #24 #28 | 指標命名定案，廢除 Detection Rate | [docs/adr/0001-p1-output-contract.md](../../docs/adr/0001-p1-output-contract.md)（決策⑨，條列項無獨立錨點） |
| SA §4.2 | #26（Purple Console 屬 P2 不屬 WS7） | 4.2 Workstream 與四區的對應 | [資安攻防平台_系統架構設計文件_v0.1.md#42-workstream-與四區的對應](../../資安攻防平台_系統架構設計文件_v0.1.md#42-workstream-與四區的對應) |
| SA §10 | #26（scenario-based，非完整 Matrix） | 10. MITRE ATT&CK 設計 | [資安攻防平台_系統架構設計文件_v0.1.md#10-mitre-attck-設計](../../資安攻防平台_系統架構設計文件_v0.1.md#10-mitre-attck-設計) |

錨點用既有已驗證模式推算（`## 2.7 P1 驗收` → `#27-p1-驗收`，README 現行連結已證實此規則）。
若某個連結在 GitHub 上沒對準，八成是標題裡的符號（`` ` ``／`：`／`&`）處理方式跟預期不同 —— 直接打開對應檔案用瀏覽器內搜尋標題文字即可，不影響地圖本身的依賴資訊。

## 開工順序

P2 內部不是 8 張平行票，是一條主鏈＋一條側支＋三個外部阻塞：

```text
#21 ──→ #22 ──→ #23 ──→ #24 ──→ #26 ──→ #27 ──→ #28
 P2-1    P2-2    P2-3    P2-4    P2-6    P2-7    P2-8
 無阻塞   │                              │
          └──────→ #25 ─────────────────┘
                    P2-5

外部阻塞（不在 #21–#28 之列，但卡住鏈上的票）：
  #18 source registry 生產路徑  → 卡 #22（source_state 輸入）、#27（來源欄動態產生）
  #20 Z-APP 四條流量規則        → 卡 #26（Console 部署在 Z-APP 需要這條防火牆規則）
  #17 response 鏈最後兩跳       → 卡 #25 的 MTTR 部分（非全票，票內已註明降級為 unknown）
```

**現在能開工的只有 #21。** 它無任何阻塞，且是後面每個數字的分母來源。

**#18 值得提前排**：它同時卡住鏈上的 #22 和 #27，是唯一卡兩張票的外部阻塞，越晚做，
越多張已完成的票會帶著「source_state 用字面值頂著」的技術債。README 已把它列進「現在可動工」。

**#20 只卡 #26 一張**，且是 WS6 的純防火牆規則（非本 workstream 邏輯），可以晚一點、
與 #23／#24 平行叫別的 agent 做。

**#17 只降級 #25 的一部分**（MTTR），不擋票本身——票的驗收標準已經寫了「#17 完成前 MTTR
為 `unknown` 而非 0」，所以 #25 不必等 #17。

## 每張票開工前該讀哪幾節

| 票 | 開工前讀 |
|---|---|
| #21 P2-1 Action Registry | §3.2（陷阱①，分母為何要凍結） |
| #22 P2-2 動作判定 | §3.2、§3.4（漏檢分兩種）、§3.5（unknown 不進分母）；程式碼 `src/purple/metrics/gaps.py` 的 `classify_miss` 已存在，不要重寫 |
| #23 P2-3 證據分級 | §3.3（C1/C2/C3 定義）、§3.6（T1005 判讀限制的具體例子） |
| #24 P2-4 三個核心數字 | §3.2（陷阱②：coverage 必須與告警總量並列）、ADR ⑨（Detection Rate 禁用） |
| #25 P2-5 延遲指標 | §3.2、§3.10 第 11 條（20 次量測 p50/p95）、ADR ⑦（三個終點不可混用）；程式碼 `src/purple/metrics/containment.py` 的 `containment_duration()` 已存在 |
| #26 P2-6 Console 畫面一 | §3.7 畫面一、§3.5（`⏳` 與 `unknown` 是同一件事）、§4.1／§3.10 第 10 條（Console 只有 Evaluation API，無 raw 查詢權）、SA §4.2（Purple Console 屬 P2 不屬 WS7）、SA §10（scenario-based，非完整 Matrix） |
| #27 P2-7 Console 畫面二 | §3.7 畫面二、§3.4（Telemetry 欄就是漏檢分類畫出來的樣子）；discuss.md 的 WAF 例子是**反面教材**，票身已引用 |
| #28 P2-8 Exercise Report | §3.8、§3.2 陷阱②、§3.4、§3.5、§4.2（retention 過期時報告怎麼標示）；程式碼 `retention/report.py` 的 `snapshot_report` 原語已存在 |
