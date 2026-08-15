# Role × UI × Permission Matrix

> 對應 [#138](https://github.com/Graylee0128/cyber/issues/138) D0。這份文件是 Participant Guide、
> Operator Guide、Technical Handbook 三份文件與 RBAC / security boundary 的共同前提。
>
> **這份文件描述現況，不是願景。** 每一格「由什麼強制」寫的都是實際擋得住的機制；
> 擋不住的一律標 ⚠️，不美化。寫得出「由什麼強制」的才是權限，寫不出來的是慣例。

## 1. 五類角色

平台的人分五類，不是紅藍紫三類。人數 R / B / P / I / A 都是未知數，由現場報名與
capacity envelope（[#137](https://github.com/Graylee0128/cyber/issues/137)）決定，不屬於權限模型。

| Role | 人數模型 | 主要 UI | 是否操作 | gateway 前綴 | clearance |
|---|---|---|---|---|---|
| 🔴 Red Player | R 人 | Red Player Portal | ✅ | `/gw/red/` | 0 |
| 🔵 Blue Player | B 人 | Blue Player Portal ＋ Blue SOC Console | ✅ | `/gw/blue/` | 1 |
| 🟣 Purple Analyst | P 人，少量內部人員 | Purple Console | ✅ 讀為主 | `/gw/purple/` | 2 |
| 👨‍🏫 Instructor | I 人，通常 1～數人 | Instructor Console ＋ Event Control | ✅ 高權限 | `/gw/instructor/` | 3 |
| 👀 Observer / Audience | A 人，不限 | Battleboard | ❌ read-only | `/gw/red/`（共用公開層） | 0 |

clearance 是線性階層 `public ⊂ blue ⊂ purple ⊂ instructor`，定義在
[`src/disclosure/clearance.py`](../../src/disclosure/clearance.py)：`red=0 / blue=1 / purple=2 / instructor=3`。

**Battleboard 是特例**：它不是任何一個 team 的工作畫面，而是整場活動的公開戰況畫面
（教室大螢幕）。它刻意走 `red / clearance 0` 的公開層前綴，讓它**沒有能力**洩漏答案——
不是靠前端不顯示，是靠後端根本不回傳。

```
                         Cyber Range
                             │
          ┌──────────────────┼─────────────────┐
          │                  │                 │
      Participants       Operations         Audience
          │                  │                 │
     ┌────┴────┐        ┌────┼────┐            │
    Red       Blue    Purple Instructor      Observer
     │          │        │       │              │
Player      Player    Purple  Instructor    Battleboard
Portal      Portal    Console   Console
                │                 │
              SOC             Event Control
```

## 2. 三個 operational plane

Blue SOC、Purple Console、Instructor Console 三者常被誤認為「同一種後台的三個版本」。
它們回答的是三個不同的問題，這個區分要固定下來：

| Plane | 誰 | 回答的問題 |
|---|---|---|
| **Defense** | Blue SOC Console | 「我藍隊現在看到什麼攻擊？我要怎麼防？」 |
| **Evaluation** | Purple Console | 「紅隊做了什麼 → 藍隊有沒有看到 → detection 有沒有命中 → telemetry 完不完整 → 這場演練效果如何？」 |
| **Exercise Control** | Instructor Console ＋ Event Control | 「整場 exercise 現在正常嗎？要不要開始、停止、reset、處理異常？」 |

推論：**Blue SOC 不該出現 coverage / gap 分析**（那是 Evaluation 的事，而且會洩漏還沒被
偵測到的攻擊）；**Purple Console 不該出現 response 按鈕**（那是 Defense 的事，紫隊按了
會污染藍隊的計分）；**Instructor Console 不該取代 Purple Console 做評估**（它管的是場次是否
正常運轉，不是演練效果好不好）。

## 3. 表一：Role × UI 可見性

- `✅` 主要工作畫面
- `👁` 唯讀 / 有限可見
- `⛔` 不可存取（設計上不給）

| | Battleboard | Red Portal | Blue Portal | Blue SOC | Purple Console | Instructor Console | Event Control |
|---|---|---|---|---|---|---|---|
| 🔴 Red Player | 👁 | ✅ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| 🔵 Blue Player | 👁 | ⛔ | ✅ | ✅ | ⛔ | ⛔ | ⛔ |
| 🟣 Purple Analyst | 👁 | ⛔ | ⛔ | 👁 | ✅ | ⛔ | ⛔ |
| 👨‍🏫 Instructor | 👁 揭露版 | 👁 | 👁 | 👁 | ✅ | ✅ | ✅ |
| 👀 Observer | ✅ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |

兩個需要說明的格子：

- **Instructor 的 Battleboard 是「揭露版」**：同一個畫面，但 `/gw/instructor/` 前綴會被 nginx
  強制加上 `?revealed=true`，公開層前綴則強制 `revealed=false`。這個開關由前綴決定，
  呼叫端說了不算（[`deploy/ui/default.conf.template:86-101`](../../deploy/ui/default.conf.template)）。
- **Purple 看 Blue SOC 是 👁 而非 ✅**：紫隊要知道藍隊看到什麼才能評估，但不該替藍隊按
  response——按了就污染被評估對象的計分。這條目前**沒有機制阻擋**，見表二。

## 4. 表二：Role × Capability

### 🔴 Red Player

| 欄位 | 內容 |
|---|---|
| 能看什麼 | 自己的 Mission（名稱／難度／時長／目標主機）、Objective 清單與完成狀態、hint 的**價格**（未購買前不含內文）、自己的分數、Battleboard 的公開層資訊、自己座位的終端機 |
| 能做什麼 | 提交 flag（`POST /api/submissions`）、購買並讀取 hint（`POST /api/hints`）、在自己的 kali 終端機內操作 |
| **不能知道什麼** | 完整 attack chain（`GET /api/scenarios` 已剝除 `attack_chain` 與 hint 內文）、藍隊看到了什麼 alert、哪些攻擊已被偵測、detection rule 內容、其他玩家的答案、Battleboard 上尚未揭露的攻擊真名 |
| 由什麼強制 | **flag／hint**：gateway `auth_request → /admission/auth/seat` 取得 `X-Seat-Source-Ip`，Range Core 只信任 gateway 代宣告的來源 IP，比對名冊（ADR 0004）。**欄位遮蔽**：`/gw/red/` 前綴 → clearance 0 → 後端組裝回應時遮蔽。**攻擊鏈**：`GET /api/scenarios` 在後端剝除（#126 P0 修正）。 |

### 🔵 Blue Player

| 欄位 | 內容 |
|---|---|
| 能看什麼 | Alert queue 與 alert 詳情（severity／service／source_ip／rule）、對應的原始 log 片段（Evidence API）、團隊 KPI（resolved／contained／平均偵測秒數）、團隊分數與反應時間表、Grafana 遙測儀表板、自己座位的兩台終端機（a=DMZ / b=內網） |
| 能做什麼 | 確認接手、判讀技法（一次性，送出即鎖定）、封鎖來源（實際派送到 Z-MGMT）、結案、標記誤報（`POST /api/blue-actions`） |
| **不能知道什麼** | 紅隊的 Objective 與 flag、hint 內文、尚未觸發 alert 的攻擊（**這是刻意的**——藍隊看不到才有 detection gap 可量）、coverage / gap 分析結果、其他隊伍的分數細節 |
| 由什麼強制 | **欄位遮蔽**：`/gw/blue/` 前綴 → clearance 1 → 後端遮蔽。**動作一次性**：`classify` 重送回 409。**計分誠實性**：`contain` 回傳 `dispatch_status`，未實際派送不計分。**⚠️ 頁面載入本身無任何機制**——見 §6 缺口 1。 |

### 🟣 Purple Analyst

| 欄位 | 內容 |
|---|---|
| 能看什麼 | ATT&CK 涵蓋率表（逐 technique 的紅隊執行／藍隊偵測狀態）、單一動作下鑽（judgement、證據等級 C1–C3、gap 分類、**逐 telemetry source** 的 ✅/❌/—）、Exercise Report 即時預覽、關聯 event 的證據內容 |
| 能做什麼 | 讀取與下鑽。**沒有任何 mutating 動作**——Purple Console 是唯讀 console | 
| **不能知道什麼** | （無額外限制；clearance 2 已可見 blue 全部與大部分 evaluation 內容）。但 **不該**替藍隊執行 response 動作 |
| 由什麼強制 | **靜態頁**：`UI_PRIVILEGED_CIDR` ＋ `auth_request → /admission/auth/instructor`。**⚠️ 這代表紫隊今天必須用教官 session 登入**——存取層沒有獨立的 purple 身分，見 §6 缺口 2。**「不該按 response」目前純屬慣例**，`/gw/blue/` 對紫隊的瀏覽器一樣開著。 |

### 👨‍🏫 Instructor

| 欄位 | 內容 |
|---|---|
| 能看什麼 | 全部。含未遮蔽的 Raw Event SSE（唯一 clearance 3 的畫面）、未匿名化的 attack chain、逐玩家分數、Battleboard 揭露版、Admission 座位告警、Grafana 存活狀態燈、SOC Copilot 的 AI 摘要（純呈現，不寫回計分／證據） |
| 能做什麼 | **Instructor Console（場次生命週期）**：開始演練、結束並重置、重掃遙測 Objective、計算延遲摘要、登出。**Event Control（座位後勤）**：設定紅藍座位上限、鎖定並建置藍隊座位（不可逆）、簽發／撤銷一次性遠端邀請連結、清理逾時領位請求、重新綁定或釋出單一座位 |
| **不能知道什麼** | 無限制（clearance 3 是最高階） |
| 由什麼強制 | **雙層**：`UI_PRIVILEGED_CIDR` 網段限制 ＋ `auth_request → /admission/auth/instructor` 教官 session cookie。登入頁 `/instructor-login/` 與 login／logout 端點刻意落在 `auth_request` 之外（否則永遠登不進去），但仍受 CIDR 約束。**⚠️ compose 預設 CIDR 是 `0.0.0.0/0`**，正式部署必須收斂到 Z-MGMT，見 §6 缺口 3。 |

**教官做不到的事（後端沒有端點，不是權限問題）**：Override Score、Inject Event。
Instructor Console 上沒有這兩個按鈕，後端也沒有對應端點，2026-08-15 v2 grilling 決議
在有真實教官需求出現前不做。另：Instructor Console 的「預備」按鈕會 403，
因為 `POST /api/exercises/prepare` 只接受 Admission 的服務身分，教官控台不是那條路徑的
合法呼叫者——這是設計，不是 bug。

### 👀 Observer / Audience

| 欄位 | 內容 |
|---|---|
| 能看什麼 | Battleboard 公開層：紅藍總分、倒數計時、**匿名化**的攻防進度（`Attack #N` 圓點，永遠不是真的 MITRE ID）、Objective 完成比例、消毒過的即時 timeline（最多 40 列） |
| 能做什麼 | 無。純觀看 |
| **不能知道什麼** | 攻擊的真實 technique 名稱、尚未揭露的攻擊狀態、「已偵測 3/4」這類分子——**分子本身就會洩漏答案**，所以未揭露前防守狀態一律收斂成中性總數 |
| 由什麼強制 | `/gw/red/eval/...` 公開層前綴 → nginx 在 `proxy_pass` 裡**寫死** `?revealed=false`，前端不帶任何參數。消毒在後端 `purple.battleboard.sanitize` 完成，不是前端不畫。 |

## 5. 強制機制總覽

三層，由外而內：

```
瀏覽器
  │  ① 網段：UI_PRIVILEGED_CIDR（只擋 /instructor/ /purple/ /event-control/ 靜態頁與教官 Admission 路徑）
  │  ② 會話：auth_request → Admission
  │       ├── /admission/auth/instructor  教官 session cookie
  │       ├── /admission/auth/seat        這個 session 坐哪台機器（flag／hint 名冊歸屬）
  │       └── /admission/auth/ttyd/{id}   這個 session 擁不擁有這台終端機
  ▼
nginx gateway（deploy/ui/default.conf.template）
  │  ③ 身分：URL 前綴 → 服務 token → clearance
  ▼
後端（Range Core / Evidence / Evaluation）
     欄位級遮蔽發生在這裡，不在前端
```

**token 從不進瀏覽器。** 前端沒有任何辦法要求更高的 clearance——它連 token 長什麼樣都不
知道。這是整個模型的地基：前端遮蔽是假的，devtools 打開就看到。

## 6. 已知缺口

以下四項是 matrix 逐格填「由什麼強制」時暴露出來的。**本票（#138）刻意不修**，只誠實記錄。

### ⚠️ 缺口 1 — `/gw/blue/` 前綴沒有任何身分檢查

`^/gw/(red|blue|purple|instructor)/core/` 這條 location 只做「前綴 → token」對應，
不做 CIDR、不做 `auth_request`。靜態頁 `/player/blue.html` 與 `/blue-soc/` 也不在
`^/(instructor|purple|event-control)/` 的保護 regex 內。

**後果**：任何能連到 Product UI 的瀏覽器，只要知道網址，就能載入 Blue SOC Console 並取得
clearance 1 的資料（alert queue、Evidence 原始 log），也能送出 `POST /api/blue-actions`。
紅隊玩家的容器在 Z-RED，需要能連到 Z-APP 才能用自己的 Player Portal，因此網路層並不隔開
這條路徑。

**目前唯一的阻力是「不知道網址」**，那不是權限。`tests/access_integration/test_ui_gateway.py`
目前也沒有任何一條斷言紅隊側瀏覽器不得取得 blue clearance 資料。

### ⚠️ 缺口 2 — 存取層沒有獨立的 Purple 身分

Purple Console 的靜態頁走 `auth_request /_auth_instructor`，也就是說**紫隊分析師今天必須
持有教官 session 才能開啟 Purple Console**。gateway 有 `purple` 前綴與 purple 服務 token，
但 Admission 沒有 purple session 的概念。

**後果**：Q1 的決策（`P 個 Purple identities → 同一個 Purple Workspace`）在存取層等於未實作；
audit log 上紫隊的動作與教官的動作分不開。

### ⚠️ 缺口 3 — 教官網段限制的預設值是全開

`UI_PRIVILEGED_CIDR` 在 compose 的預設值是 `0.0.0.0/0`（本機 demo 用）。它是 `auth_request`
之外的第二層，但預設狀態下等於不設防。**正式部署必須收斂到 Z-MGMT 網段**——這是 Operator
Guide 的 pre-flight checklist 項目，不是可選項。詳見 `ui/README.md` 已知缺口 2 與
[#126](https://github.com/Graylee0128/cyber/issues/126)。

### ⚠️ 缺口 4 — Evaluation API 自己不驗證身分

`^/gw/(red|blue|purple|instructor)/eval/` 這條 location **不注入任何 token**——Evaluation API
本身不做身分驗證。它之所以安全，是因為它只住 Z-MGMT、不對外發佈，瀏覽器唯一到得了它的
路徑就是這條 gateway。**這個假設一旦破了（例如把 8002 port 對外開），Evaluation 的全部
資料就是公開的。**

## 7. 決策紀錄

四個決策在 [#138](https://github.com/Graylee0128/cyber/issues/138) 提出，結論如下。

### Q1 — Purple 不共用一組帳號

**結論：採預設。** 產品概念是 `1 Purple Workspace`，IAM / audit 概念是
`P 個 Purple identities → 同一個 Purple Workspace`。

理由：exercise report 或 automated response 出問題時，共用帳號查不出「是哪個內部人員做的」。

**現況**：未實作（見缺口 2）。此決策記錄目標狀態，實作待另開票。

### Q2 — Instructor 授權維持現況，缺口誠實揭露

**結論：本票不實作 Admission 的 instructor 身分端點。** matrix 的「由什麼強制」欄與 Operator
Guide 都寫明 `⚠️ 網段 ＋ 教官 session cookie，預設 CIDR 全開`，Technical Handbook §13 列為
已知缺口。

理由：實作 instructor session 端點會讓 #138 的範圍膨脹到跨越文件與 RBAC 兩件事。

### Q3 — Blue Portal 與 Blue SOC 的分工

**結論：採預設，只寫文件不改實作。**

- **Blue Player Portal ＝ 個人視角**：我的任務說明、我的兩台終端機、團隊 KPI 摘要、團隊分數。
  它是任務 HUD。
- **Blue SOC Console ＝ 團隊防禦工作台**：全隊的 alert queue、Evidence、timeline、response
  動作、Grafana。

兩者同為 `blue` 身分、同一 clearance，差別是資訊密度與用途，不是權限。

### Q4 — Observer 不另開 gateway 身分

**結論：採預設，維持共用 `red / clearance 0`。**

Battleboard 的延遲揭露由前綴強制寫死 `revealed=false`，已足夠。另開 `observer` 前綴的好處
是 audit log 分得開，成本是多一組 token 與一條 map 條目——目前沒有需要區分 observer 與 red
的 audit 需求。

## 8. 與其他文件的關係

| 文件 | 關係 |
|---|---|
| [Participant Guide](../participant-guide/README.md) | 玩家能做的每個動作，都必須在本文件表二的「能做什麼」欄找得到 |
| [Operator Guide](../operator-guide/README.md) | 缺口 3 是 pre-flight checklist 項目；教官與紫隊的操作範圍出自表二 |
| [Technical Handbook](../technical-handbook/README.md) | §5 UI Architecture 與 §6 Identity / Authorization 連到本文件，不重述 |
| [`ui/README.md`](../../ui/README.md) | 實作層的已知缺口清單；本文件的缺口 1、2 是 matrix 新發現的，不在該清單內 |
| [ADR 0004](../adr/0004-roster-attribution-via-trusted-gateway.md) | 表二 Red 列「由什麼強制」的 `X-Seat-Source-Ip` 機制出處 |
