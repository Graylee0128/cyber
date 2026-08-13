# Spec — WS5 Cyber Range Core

- **Status**: draft（尚無票消費，定案後比照 P1 遷出至 `docs/`；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-11 grilling session（五題逐一確認 ＋ 一個防禦性推論，本檔為落地記錄）
- **上位文件**：[SA](../../資安攻防平台_系統架構設計文件_v0.1.md) §8（Cyber Range Core）、§15（API Boundary）、§18（Open Questions）
- **上游依賴**：[WS1 遊戲規則 spec](../ws1-game-design/spec.md)（計分模型、Objective 判定、結束條件由該檔定義）
- **既有契約**：[docs/p1-output-contract.md](../../docs/p1-output-contract.md)（Core Event Schema 已定版，WS5 是其下游消費者）

WS5 是 SA §4.1 產品線的中段（WS1 → **WS5** → WS7）。它的上游規則已由 WS1 spec 定案，
本檔處理的是「規則落成系統」時 SA §8／§15／§18 留下的待決問題。

---

# 1. 量測與計分

## 1.1 Blue 反應時間由 WS5 自量，不取用 P2 的數字

Blue objective「Contain < 60 sec」（SA §9）的判定，量的是
**從 Core Event（偵測發生）到 Blue Action 進入 Event Service 的時間差**——兩端都是
WS5 自己掌握的資料。

**不得使用的兩個數字**：

| 數字 | 為什麼不能拿來計分 |
|---|---|
| P2 的 **MTTR** | 終點是「response 在靶機真的生效」，中間含 agent 輪詢間隔、ipset 套用等**藍隊控制不了的基礎設施延遲**。拿它計分等於用系統效能評人 |
| P2 的 **containment duration** | [containment.py](../../src/purple/metrics/containment.py) 的 docstring 已寫死：「量的是攻擊從被偵測到停止有多久，**與藍隊做了什麼無關**」。攻擊者自己收手也會 resolved，藍隊什麼都沒做也會拿分 |

**這不是重複計算**：P2 的 MTTR 回答「這套系統處置得多快」（紫隊報告用），
WS5 的反應時間回答「這個藍隊隊員反應多快」（計分用）。兩個不同的觀察對象。

此決策同時維持 [WS1 spec §1.2](../ws1-game-design/spec.md)「Purple 不參與計分」不被架空——
若 Blue 分數要靠 P2 的數字算，P2 就實質成為計分輸入了。

## 1.2 Blue Action 記在 event 級，incident 層級用聚合推導

Blue Action 事件以 `event_id`（單一偵測事件）為歸屬粒度，**不以 incident 為粒度**。

**為什麼現在就能決定，不必等 WS3**：Incident 屬 WS3 Blue Operations（未開始），
其最終形狀未定。但細粒度可以聚合成粗粒度，反過來不行——WS3 之後不論把 incident
定義成什麼形狀，都能從 event 級記錄聚合出 incident 級反應時間；若現在記在 incident 級，
event 級的資訊就永久遺失。與 §1.3、§2.1 是同一條邏輯。

**WS5 需要一個最小的 Blue Action 進場契約**（誰、何時、對哪個 `event_id`）。
動作的分類法（isolate／block／escalate／close 等）屬 WS3，不在本檔範圍。
SA §8.3 的 Event Service 輸入來源清單本來就列了 `Blue Action`——這是補上契約，不是新增架構。

## 1.3 分數是推導出來的，不存欄位

沒有 `score` 欄位。分數在讀取時由「完成了哪些 objective ＋ 用了哪些 hint」即時算出。

**為什麼**：

1. 存起來的分數會**靜默地漂**——欄位寫 450、objective 加總是 500，沒有任何機制會發現。
   與本專案「不接受宣稱、只接受證據」的基調直接矛盾。
2. 與 P2 一致：[src/purple/metrics/](../../src/purple/metrics/) 底下沒有任何一個存起來的指標，
   `containment_duration()` 吃事件算、`classify_miss()` 是純函式。兩個 workstream 同一種模式。
3. 規模上沒有理由做這個優化——一場演練 30 分鐘、數十筆 objective 完成記錄，讀取時加總是微秒級。
4. 天然支援 SA §16 V3 的 Replay：分數是事件的函數，重播事件即重建分數。
5. Hint 扣分（[WS1 spec §3](../ws1-game-design/spec.md)）在推導模型下特別自然——
   分數是 `f(完成的 objective, 用掉的 hint)`，不需要在 hint 被索取當下回頭改一個已寫死的欄位。

未來若真有讀取壓力，從推導加一層快取是純增量；從儲存退回推導才是重工。

---

# 2. Exercise 生命週期

## 2.1 同時只有一場演練在跑，但 schema 保留 `exercise_id` 維度

- **schema**：所有演練相關資料帶 `exercise_id`
- **執行期**：以 PostgreSQL partial unique index（`WHERE state = 'running'`）在資料庫層強制
  「同時只有一場 running」，不靠應用程式自律

**為什麼不做全域單例**：`exercise_id` 這個 key **已經存在且 P1 已經在用**——
`core_events` 有 `exercise_id NOT NULL` 欄位與 `core_events_exercise_idx (exercise_id, observed_at)`
複合索引（[store/db.py](../../src/purple/store/db.py)）。改用全域單例等於主動放棄既有結構，
未來要支援多場時得把 `exercise_id` 補回每一張表與每一個查詢——那是 blast radius 會炸開的 wide refactor。

**為什麼不做並行多場**：實體上只有一套 range（四區 VLAN、一台靶機 VM、六台 kali 全部共用），
兩場同時跑會互相污染靶機狀態與遙測。SA §16 也把 Multi-Team／Multi-Scenario 歸在 V3。

**日後要放寬到並行時**，成本不在 schema（本決策已預先付掉），而在實體 range 複製（WS6）
與事件歸屬邏輯（見 §2.2），選單例也一樣要付這兩筆。

## 2.2 `exercise_id` 必須於開演時注入偵測鏈（既有缺陷）

**現況是錯的**：[receiver/core.py](../../src/purple/receiver/core.py) 從 Grafana rule 的 label 讀
`exercise_id`，而 [rules.yaml](../../deploy/grafana/provisioning/alerting/rules.yaml) 四條規則
**全部硬寫 `ex-001`**。

**後果**：跑第二場演練時，其事件仍會被標成 `ex-001`。**不需要等到並行，跑第二場就錯。**

修法沒有第二種選擇：演練開場時把當前 `exercise_id` 注入偵測鏈，取代硬寫的常數。
此項應由 Exercise 生命週期相關的票連帶解決。

## 2.3 Reset 是兩層，不焊死在一起

| 層 | 動作 | 成本 | 歸屬 |
|---|---|---|---|
| 演練狀態 | `POST /api/exercises/reset` —— 清分數／objective／exercise 狀態 | 毫秒級（幾個 DELETE） | **WS5** |
| 環境 | `range-reset.sh` —— teardown ＋ range-up，重建 VLAN 與靶機 VM | 分鐘級 | **WS6** |

**為什麼不合併**：

1. 成本差三個數量級。綁在一起，每次想重跑計分測試都要等靶機重開。
2. 職責邊界與 SA §4.2 一致——環境是 WS6 的產出。API 直接驅動 WS6 腳本，等於 Z-APP 的服務
   需要操作 host 上 OVS／libvirt 的權限，是明顯的權限擴張。
3. 合併會讓「重置」變成有時毫秒、有時數分鐘的操作，呼叫端無法預期。

**「全部重來」仍然做得到**——由 Instructor Console（WS7）依序呼叫兩者，那是編排層的責任，
不需要在資料層焊死。

### #32 實作契約

- `POST /api/exercises/reset` 不接受 score 或 objective override；沒有 running exercise 時回 `404`。
- reset 刪除當前 Exercise aggregate。名冊、objective completion 與 hint usage 都以
  `exercise_id` 外鍵 `ON DELETE CASCADE`，因此同一個 transaction 內清除。
- `core_events` 不對 Exercise aggregate 建 cascade 外鍵；reset 後仍保留稽核軌跡。
- `reset_scope: environment` 只是在 `GET /api/scenarios` 告知編排者還需另跑 WS6 reset；
  本 API 永遠不執行 `scripts/range/**`。
- `GET /api/score` 與 completion/hint 的業務寫入由 #33 提供；#32 只建立其
  exercise-scoped lifecycle schema，不建立可寫的 `score` 欄位。

## 2.4 結束條件沿用 WS1

純時間制，固定 `duration`，不因 Red 完成度提早結束——理由見
[WS1 spec §5.1](../ws1-game-design/spec.md)（可比性：每場演練觀察窗口需等長）。

Instructor 手動中止屬 WS7 操作層，不是遊戲規則。

---

# 3. API

## 3.1 Realtime 用 SSE，不用 WebSocket

`SSE /api/events/live`（SA §15 已列為「或先使用」的選項）。

**為什麼**：

1. **沒有任何一條資料流需要 client → server 串流。** 需要推的（Battleboard 分數與攻擊鏈進度、
   Blue SOC 事件流、Player Portal 完成回饋）全部是 server → client；反方向的動作
   （提交 flag、Blue Action、instructor 控制、索取 hint）都是離散的一次性請求，POST 即可。
   紅隊的攻擊是**繞過平台直接打靶機**（VLAN30 → VLAN20，不經 Z-APP），不透過 Portal 發動。
2. **SSE 自帶重連**。演練中斷線重連是常態（投影幕機器、學員筆電），`Last-Event-ID` 可直接續傳
   漏掉的事件；WebSocket 這套要自己實作。
3. **跨區流量更單純**。SA §12.3 定義 Red 走 `RED → APP :443`，SSE 就是普通 HTTPS 長連線，
   不需為 upgrade handshake 在防火牆規則上開特例。

日後若真需要雙向，事件推播的 schema 不變、只換傳輸層，不算重工。

## 3.2 Endpoint 沿用 SA §15 草案

`GET /api/exercises/current` 在 §2.1 之下照樣成立——回傳唯一那場 running 的演練，
內部仍以 `exercise_id` 定址。

---

# 4. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼不在本檔決定 |
|---|---|
| Blue Action 的分類法（isolate／block／escalate…） | 屬 WS3 Blue Operations。WS5 只需要進場契約（誰、何時、對哪個 event_id），見 §1.2 |
| Incident 模型 | 屬 WS3。§1.2 的 event 級記錄保證日後可聚合 |
| Redis / PubSub（SA §18 Core） | 規模到了才需要的實作選型，不是架構決策 |
| Core Event 保留期（SA §18 Core） | P1 的 ADR ⑩ 已處理 raw log；Core Event 本身小且需供報告引用，持久保存無爭議 |
| Objective 完成可否撤銷 | 實作層細節。§1.3 的推導模型下，附加一筆反轉記錄即可，不影響 schema |
| Multi-team／Multi-tenant／Replay | SA §16 歸在 V3，範圍外 |

---

# 5. 決策總表

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 1 | Blue「Contain < 60s」誰量、量什麼 | WS5 自量反應時間（Core Event → Blue Action），不碰 P2 的 MTTR／containment | §1.1 |
| 2 | 單場 vs 並行多場 | schema 帶 `exercise_id`，PG partial unique index 強制同時一場 | §2.1 |
| 3 | Exercise reset 語意 | 兩層分離；API reset 只清演練狀態，環境重建屬 WS6 | §2.3 |
| 4 | Realtime 傳輸 | SSE | §3.1 |
| 5 | 分數怎麼存 | 推導，不存欄位 | §1.3 |
| — | Blue Action 歸屬粒度（防禦性推論） | event 級，incident 用聚合推導 | §1.2 |
| — | `exercise_id` 硬寫 `ex-001`（既有缺陷） | 開演時注入，取代常數 | §2.2 |
