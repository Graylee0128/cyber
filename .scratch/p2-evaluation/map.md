# P2 Evaluation & Console — 執行導覽

執行進行中的地圖，做完後比照 P1 遷入 `archive/`（見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）。
現行票在 GitHub Issues（#21、#26、#28），契約散在票身裡；這份地圖不重複票的內容，只做兩件事：
把票裡的相對引用接到真實錨點，把跨票依賴畫成一張圖。

## 錨點對照表

票身裡的 `§x.y` 都是相對引用，指向 [purple_platform_plan.md](../../purple_platform_plan.md)
或 SA（[資安攻防平台_系統架構設計文件_v0.1.md](../../資安攻防平台_系統架構設計文件_v0.1.md)）。
下表把現行票裡的引用接到真實錨點（GitHub heading slug：句號與 `&` 直接移除不留空格、空白轉連字號、大小寫轉小寫）。

| 票內引用 | 出處 | 標題 | 錨點 |
|---|---|---|---|
| §3.2 | #21 | 3.2 計分模型 | [purple_platform_plan.md#32-計分模型](../../purple_platform_plan.md#32-計分模型) |
| §3.3 | #21 | 3.3 證據分級 | [purple_platform_plan.md#33-證據分級](../../purple_platform_plan.md#33-證據分級) |
| §3.4 | #21 #26 #28 | 3.4 兩種漏檢必須分開 | [purple_platform_plan.md#34-兩種漏檢必須分開](../../purple_platform_plan.md#34-兩種漏檢必須分開) |
| §3.5 | #21 #26 #28 | 3.5 `unknown` 是合法狀態 | [purple_platform_plan.md#35-unknown-是合法狀態](../../purple_platform_plan.md#35-unknown-是合法狀態) |
| §3.6 | #21（technique 判讀限制） | 3.6 ATT&CK 對應要寫判讀限制 | [purple_platform_plan.md#36-attck-對應要寫判讀限制](../../purple_platform_plan.md#36-attck-對應要寫判讀限制) |
| §3.7 | #26 | 3.7 Purple Console 只有兩個畫面 | [purple_platform_plan.md#37-purple-console-只有兩個畫面](../../purple_platform_plan.md#37-purple-console-只有兩個畫面) |
| §3.7 畫面一 | #26 | 畫面一：ATT&CK Coverage 表 | [purple_platform_plan.md#畫面一attck-coverage-表](../../purple_platform_plan.md#畫面一attck-coverage-表) |
| §3.7 畫面二 | #26 | 畫面二：單一 technique 下鑽 | [purple_platform_plan.md#畫面二單一-technique-下鑽](../../purple_platform_plan.md#畫面二單一-technique-下鑽) |
| §3.8 | #28 | 3.8 Exercise Report 是 P2 的收尾產出 | [purple_platform_plan.md#38-exercise-report-是-p2-的收尾產出](../../purple_platform_plan.md#38-exercise-report-是-p2-的收尾產出) |
| §3.10 | #21（第 11 條）#26（第 10 條） | 3.10 P2 驗收 | [purple_platform_plan.md#310-p2-驗收](../../purple_platform_plan.md#310-p2-驗收) |
| §4.1 | #26（Console 只有 Evaluation API） | 4.1 Evaluation Engine 必須有 raw 查詢權 | [purple_platform_plan.md#41-evaluation-engine-必須有-raw-查詢權](../../purple_platform_plan.md#41-evaluation-engine-必須有-raw-查詢權) |
| §4.2 | #28（retention 過期的報告顯示） | 4.2 raw log 保留是有成本的 | [purple_platform_plan.md#42-raw-log-保留是有成本的](../../purple_platform_plan.md#42-raw-log-保留是有成本的) |
| ADR ⑦ | #21 | MTTR 終點＝response 生效 | [docs/adr/0001-p1-output-contract.md](../../docs/adr/0001-p1-output-contract.md)（決策⑦，條列項無獨立錨點） |
| ADR ⑨ | #21 #28 | 指標命名定案，廢除 Detection Rate | [docs/adr/0001-p1-output-contract.md](../../docs/adr/0001-p1-output-contract.md)（決策⑨，條列項無獨立錨點） |
| SA §4.2 | #26（Purple Console 屬 P2 不屬 WS7） | 4.2 Workstream 與六區的對應 | [資安攻防平台_系統架構設計文件_v0.1.md#42-workstream-與六區的對應](../../資安攻防平台_系統架構設計文件_v0.1.md#42-workstream-與六區的對應) |
| SA §10 | #26（scenario-based，非完整 Matrix） | 10. MITRE ATT&CK 設計 | [資安攻防平台_系統架構設計文件_v0.1.md#10-mitre-attck-設計](../../資安攻防平台_系統架構設計文件_v0.1.md#10-mitre-attck-設計) |

錨點用既有已驗證模式推算（`## 2.7 P1 驗收` → `#27-p1-驗收`，README 現行連結已證實此規則）。
若某個連結在 GitHub 上沒對準，八成是標題裡的符號（`` ` ``／`：`／`&`）處理方式跟預期不同 —— 直接打開對應檔案用瀏覽器內搜尋標題文字即可，不影響地圖本身的依賴資訊。

## 開工順序

P2 已整併為三張現行票，依序交付後端、Console、Report：

```text
#21 ──→ #26 ──→ #28
 後端      Console   Report

外部阻塞：
  #42 WS2 schema 遷移          → 卡 #21（attack_chain／not_executed 輸入契約）
  #18 source registry 生產路徑 → 卡 #21（source_state 輸入）與 #26（來源欄動態產生）
  #20 Z-APP 四條流量規則       → 卡 #26（Console 部署在 Z-APP 需要這條防火牆規則）
```

**先清 #42，再做 #21。** #21 現在統一擁有 Action Registry、動作判定、證據分級、核心數字與延遲指標；
#18 提供它需要的 source registry 生產輸入。#21 完成後才進 #26，並先確保 #20 的 Z-APP 網路規則可用。

歷史整併紀錄：原 #22–#25 已吸收進 #21；原 #27 已吸收進 #26。這些號碼不再代表可獨立開工的票。

## 每張票開工前該讀哪幾節

| 票 | 開工前讀 |
|---|---|
| #21 P2 後端整合 | §3.2–§3.6、§3.10 第 11 條、ADR ⑦／⑨；沿用 `src/purple/metrics/gaps.py` 的 `classify_miss` 與 `src/purple/metrics/containment.py` 的 `containment_duration()`，不要重寫 |
| #26 Purple Console（畫面一＋畫面二） | §3.7、§3.5、§4.1、§3.10 第 10 條、SA §4.2、SA §10；discuss.md 的 WAF 例子是**反面教材** |
| #28 P2-8 Exercise Report | §3.8、§3.2 陷阱②、§3.4、§3.5、§4.2（retention 過期時報告怎麼標示）；程式碼 `retention/report.py` 的 `snapshot_report` 原語已存在 |
