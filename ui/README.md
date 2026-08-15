# Product UI

平台的七個對人畫面（Player Portal 紅藍各一檔）。零依賴靜態頁（無 build step、無 npm），
由 nginx 服務並代理後端。

| 畫面 | 目錄 | Audience | gateway 身分 |
|---|---|---|---|
| Battleboard | `battleboard/` | 全場、教室大螢幕 | `red`（clearance 0） |
| Player Portal — Red | `player/index.html` | Red | `red` |
| Player Portal — Blue | `player/blue.html` | Blue | `blue` |
| Blue SOC Console | `blue-soc/` | Blue | `blue` |
| Purple Console | `purple/` | 分析師、Instructor | `purple` |
| Instructor Console | `instructor/` | Instructor | `instructor` |
| Event Control | `event-control/` | 會議中控 | `instructor` → Admission |

**誰能看什麼、能做什麼、由什麼強制**：見
[Role × UI × Permission Matrix](../docs/architecture/role-ui-permission-matrix.md)。
那份文件也記錄了 matrix 逐格填「由什麼強制」時發現、而下方已知缺口清單沒有涵蓋的兩個缺口。

**技術選型（為什麼是零依賴靜態頁）與權限模型（token 不進瀏覽器、一個頁面只能有一個身分）**
已遷入 [Technical Handbook §5 UI Architecture](../docs/technical-handbook/README.md#5-ui-architecture)
與 [§6 Identity / Authorization](../docs/technical-handbook/README.md#6-identity--authorization)。
權限邊界的實體仍是 [`deploy/ui/default.conf.template`](../deploy/ui/default.conf.template)。

本檔保留的是**實作導覽**：怎麼跑起來、完成度對照、已知缺口。

## 跑起來

```bash
docker compose --profile admission-e2e up -d --build ui evaluation-api admission-range-core admission admission-receiver
```

畫面在 <http://localhost:8090/>。

Blue SOC 的 Grafana 分頁與 Evidence 查詢需要**預設 profile 也起著**（`grafana`、
`evaluation-engine`、`loki` 住在那邊）：

```bash
docker compose up -d grafana evaluation-engine loki
```

注意兩個 profile 各自帶一座 PostgreSQL，狀態不共用 —— 跨 profile 查證據時，
`evaluation-engine` 讀的是預設 profile 那座庫，access plane 產生的 event id 在那裡查不到。
要完整的證據鏈就得讓兩邊指向同一座庫，那屬部署決策，不在本次範圍。

## 完成度對照

逐項對照 SA 的畫面清單與 `.scratch/` 六份視覺提案。`△` 是有做但受限於後端資料面，
`✗` 是後端沒有對應能力、做不了。

**Battleboard**（SA §5.4 的八個 widget）

| widget | 狀態 | 備註 |
|---|---|---|
| Match Header | ✅ | |
| Red-Blue Score | ✅ | |
| Round Timer | ✅ | 由 `ends_at` 每秒倒數 |
| Attack Chain Progress | ✅ | 匿名化為 `Attack #N` |
| Scenario-based MITRE ATT&CK | △ | 與上一項合併。**沒有依 tactic 分階段**（mock 有 Initial Access／Execution／…）—— sanitized 投影不回 tactic，而 tactic 本身就會洩漏這關考什麼 |
| Defense Status | △ | 依 Q4 決策縮成中性計數。揭露前不顯示「已偵測 3/4」——那個分子就是還沒公開的答案 |
| Live Timeline | ✅ | |
| Objective Progress | ✅ | |

**Player Portal**（SA §5.1 功能清單）

Mission ✅／Target ✅／Difficulty ✅／Hint ✅（先講扣分）／Objective ✅／Score ✅／
RemainingTime ✅／FlagSubmission ✅／Shell ✅（iframe 到 Z-EDGE 的 ttyd 路由）。
Login `✗` —— 領位與登入屬 Admission 的 join 流程，不在這一頁。

**Blue SOC Console**

Alert ✅／Evidence ✅／Timeline ✅／Response Action ✅（五個動作的封閉列舉，判讀類送出即鎖）／
Grafana ✅（依 spec Q2 決策，純嵌入不重做 incident 佇列）。
Incident △ —— 後端沒有 incident 聚合模型，佇列的單位是 event 不是 incident。

**Purple Console**

畫面一 ✅／畫面二 ✅（逐來源 Telemetry 欄，#126）／Exercise Report ✅ 即時預覽（持久化快照屬 #28，是另一份東西）。

**Instructor Console**

生命週期 ✅／即時真實攻防狀態 ✅／Raw Event ✅／維運動作 ✅／SOC Copilot ✅（#133，
把 Admission 告警唸成一段 AI 摘要，純呈現層，不寫回任何計分／證據欄位；AI 服務
沒起或逾時時這裡空著，其餘功能不受影響）。
**Override Score 與 Inject Event `✗`** —— 後端完全沒有這兩條端點（全部路由清點過，沒有
override／inject 路徑），所以做不了。spec Q3 已定案「要留稽核、與 #55 統一管道」，
但那是對還不存在的功能所做的決策。**2026-08-15 v2 grilling 拍板：暫不開票**，目前 8 個
workstream 的契約都不需要，等真的有教官在演練中要求改分的實例出現再開（見
`purple_platform_plan.md` §7.2 Q5）。

**Event Control**

座位池上限與鎖定 ✅／一次性邀請連結簽發與撤銷 ✅／座位告警 ✅／單一座位 rebind 與 release ✅／
逾時清理 ✅。完整座位表 △ —— Admission 只有伺服器端渲染的 HTML 版（`/admission/instructor/{id}/console`），
沒有 JSON 端點，所以連出去而不是再刻一份會漂掉的表。

## 已知缺口

這些是**真的沒做**，不是沒寫完。放在這裡是為了它們不會被畫面的完整度掩蓋掉。

### 1. ~~來源 IP 歸屬與反向代理相衝~~（已修，2026-08-15）

`POST /api/submissions` 與 `POST /api/hints` 用 TCP peer address 當名冊的鍵，且**刻意不信任**
`X-Forwarded-For`（`range_core/api.py` 的 `_source_ip`：信了就等於讓一個紅隊玩家能用別人的
身分提交 flag）。經 gateway 進來的請求，Range Core 看到的是 nginx 的位址，所以會回
403 `source IP is not on the exercise roster`。

**2026-08-15 修復**（[#126](https://github.com/Graylee0128/cyber/issues/126) item 4）：解法不是
「開始相信某個標頭」，而是只信任**一個**呼叫者。gateway 對這兩條端點先 `auth_request` 問
Admission 的 `/admission/auth/seat`「這個 session 坐哪台機器」，把答案放進 `X-Seat-Source-Ip`；
Range Core 只在 TCP peer 真的是 `RANGE_CORE_TRUSTED_EDGE_HOST` 解析出來的位址時才採信該標頭，
其餘一律看 peer。紅隊玩家直連 Range Core 自己塞標頭沒有用——他的 peer 是自己那台 kali。

為什麼由 gateway 而不是 Z-EDGE 做：代宣告來源 IP 需要握有 Range Core 的服務 token，而
Z-EDGE 必須維持零憑證（WS8 spec §5.3，`tests/deploy/test_edge_access.py` 有測試在管）。
gateway 本來就依設計持有全部服務 token，這條路徑沒有讓任何主機多拿到一份秘密。

### 2. 誰能載入教官畫面 —— 已補第二層，但預設值仍全開（部分已修，2026-08-15）

原狀是 `UI_PRIVILEGED_CIDR` 為**唯一**擋得住「拿到網址就進得去 Instructor Console」的東西：
Admission 當時只回答「這個 session 擁不擁有這台終端機」（`/admission/auth/ttyd/{terminal}`），
沒有「這個 session 是不是教官」的端點，nginx 的 `auth_request` 接不上去。

**2026-08-15 修復**（[#126](https://github.com/Graylee0128/cyber/issues/126) item 2）：重用既有的
`ADMISSION_INSTRUCTOR_TOKEN`（不是新密鑰）加出 `/admission/auth/instructor`，nginx 對
`^/(instructor|purple|event-control)/` 補上 `auth_request`，並新增登入頁 `ui/instructor-login/`。
登入頁與 login／logout 端點刻意落在 `auth_request` 之外——被自己要求的 cookie 擋住就永遠登不進去。
**CIDR 是補強不是取代**，兩層都在。

**仍然沒解的**：compose 裡的 `UI_PRIVILEGED_CIDR` 預設值是 `0.0.0.0/0`（本機 demo 用），
**正式環境必須收斂到 Z-MGMT 網段**。這是部署設定，不是程式碼——它會一直是 pre-flight 項目。

### 3. ~~`GET /api/scenarios` 會吐出攻擊鏈~~（已修，2026-08-15）

那條端點回的是完整 scenario 扣掉 hint 文字 —— `attack_chain`（每一步的 MITRE technique
與描述）曾留在回應裡，紅隊直接打這條端點就繞過 Battleboard 的匿名化拿到完整解答。
**2026-08-15 v2 grilling 拍板：最高優先修**，已在 `range_core/api.py` 的 `list_scenarios()`
比照 hint 文字補投影移除，補了 mutation-proof 測試（[#126](https://github.com/Graylee0128/cyber/issues/126)）。

### 4. ~~逐來源的 Telemetry 欄沒有出口~~（已修，2026-08-15）

`purple/console/drilldown.py` 的 ✅／❌／—／⏳ 判定邏輯本來完整但從未被接線。
**2026-08-15 v2 grilling 拍板：排 backlog**，已接上 `assembly.py`（`_telemetry_detail` 同一次
查詢順帶記錄每來源有沒有命中）／`evaluator.py`（`ActionResult.source_marks`）／
`evaluation/api.py`（`GET .../evaluation` 的每筆 action 附 `telemetry` 陣列，呼叫既有的
`telemetry_mark()`），Purple Console 畫面二直接渲染（[#126](https://github.com/Graylee0128/cyber/issues/126)）。

### 5. briefing 沒有 API

`scenarios/<id>/briefing.md` 是檔案，沒有任何 HTTP 出口。Player Portal 的 Mission 面板
目前只顯示 scenario 的中繼資料（名稱、難度、時長、目標主機）。

### 6. 未登記的 scenario 在 Evaluation API 上回 503（原文已過時，2026-08-15 重寫）

> **原本這條寫的是「對任何真 scenario 都會 500，因為 `config/scenario-sources.yaml` 刻意留空」。
> 兩個前提現在都不成立**——寫下它的 PR #110 與回填清單的 PR #112（#47）只差 14 分鐘合併，
> 兩件事在同一天各自往前走，這條就沒跟上。留下更正紀錄而不是默默改掉。

現況：

- `config/scenario-sources.yaml` 的 `scenarios:` **已經不是空的**，[#47](https://github.com/Graylee0128/cyber/issues/47)
  （PR #112）登記了 `shopdb-credential-pivot`。**真 scenario 走得通。**
- `purple/evaluation/api.py` 的 `_session()` **已經接住 `CatalogError`**，回 **503** 而不是未攔截的 500
  ——呼叫端因此分得出「暫時算不出來」與「程式壞了」，也不洩漏 traceback。

**還在的問題**：沒有登記在 `scenarios:` 區塊的 exercise（例如 `admission-e2e`、
`p2-latency-baseline` 這類量測載具）打 `GET /api/exercises/{id}/evaluation` 或
`.../battleboard` 會拿到 503，Purple Console 的涵蓋率表與 Battleboard 的攻防進度是空的，
不是零資料。fixture（`sqli-01`／`bruteforce-01`／`falco-*`，見同一份 YAML 的 `fixtures:` 區塊）
不是 scenario，`registry_for_scenario` 查不到它們——這是 [#43](https://github.com/Graylee0128/cyber/issues/43)
刻意的區分，不是缺陷。

**真正的殘留設計債**是 `expected_sources` 有兩個真相來源：scenario 的 `metadata.yaml` 裡有一份，
這份獨立清單裡也有一份，兩邊必須手動保持一致。統一它屬另一票。

## 後端出口：判定邏輯有、部署裡沒有

三個「判定邏輯早就寫好、但部署裡沒有出口」的縫，由 PR #110 一併補上：

- `GET /api/techniques` —— technique 判讀限制（#26 的 acceptance criteria 要求常駐顯示，
  而那段文字只存在於 `config/techniques.yaml`）
- `GET /api/exercises/{id}/battleboard` —— `purple.battleboard.sanitize`（#82）此前沒有
  任何呼叫者
- compose 的 `evaluation-api` service —— **Evaluation API 從來沒有被部署過**。既有的
  `evaluation-engine` 名字像它，跑的其實是 `purple.evidence.service`

第三項與 #51 那兩個環境變數是同一類問題：單元測試全綠，功能在真部署裡不存在。
[`tests/access_integration/test_ui_gateway.py`](../tests/access_integration/test_ui_gateway.py)
就是為了讓這類縫會變紅而存在。
