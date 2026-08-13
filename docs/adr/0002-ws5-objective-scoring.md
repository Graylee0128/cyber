# ADR 0002 — WS5 Objective & Scoring：telemetry_signal 形狀、Blue 分數、API 形狀、hint 疊加

- **狀態**：Accepted
- **日期**：2026-08-13
- **決策者**：gray（經 planner agent 產出選項，人確認）
- **產生方式**：`/plan` for #33
- **影響範圍**：[WS1 spec](../../.scratch/ws1-game-design/spec.md) §1.1／§1.3／§2.2、[WS5 spec](../../.scratch/ws5-range-core/spec.md)、`src/range_core/scenarios.py`、`src/range_core/exercises.py`
- **實作**：#33（PR 待建）

---

# 1. 背景

#33 是合併 #34、#35 的 canonical work package，要在 Range Core 加上 telemetry／submission
兩條 objective 完成路徑、hint 記錄、即時計分。實作前有四個規格沒定案的缺口，其中一個
（telemetry_signal）不定案就無法動 schema，其餘三個會決定 API 形狀與計分公式，錯了要
重寫呼叫端。

# 2. 決策總表

| # | 決策 | 選定 | 被否決 |
|---|---|---|---|
| ① | Objective 如何宣告是哪個 Core Event 完成它 | **nested `telemetry_signal: {action_id}`** | 扁平 `action_id: str \| None`／隱式 objective id == action id／技法比對 |
| ② | `GET /api/score` 的 Blue 欄位 | **v1 只回 `red`，Blue 待 WS3 交付 Blue Action 契約後再加** | `{"blue": null}` 顯式佔位／現在就建 Blue 計分 |
| ③ | 新 endpoint 形狀 | **專用 `POST /api/submissions`、`POST /api/hints`** | 通用 `POST /api/actions`（SA §15 草案） |
| ④ | 同一 objective 疊兩個 hint，罰分怎麼算 | **取最大 `penalty_percent`** | 加總＋100% 上限／乘法複合 |

# 3. 決策細節

## ① telemetry_signal：nested，不是扁平欄位，不是隱式同名

**為什麼不能隱式（`Objective.id == AttackAction.id`）**：拼字打錯會讓一個 objective
永遠完成不了，而且不會有任何錯誤 —— `extra="forbid" + frozen=True` 存在的目的就是不許
這種靜默失敗（WS2 spec §7）。

**為什麼不能比對 technique**：`purple/store/events.py` 的 `CoreEventStore.detections_by_action`
明文拒絕「technique ＋ 時間鄰近」推導 ——「技法與時間窗鄰近性在這裡完全不參與」「沒帶關聯鍵
的事件不得被拿去對應任何註冊動作，這正是本方法只走 action_id 的理由」。一個 technique 可能
對應多個註冊動作，猜「最近的那個」會把證據配到錯的動作上，而且錯得無聲無息。Objective 若帶
`technique:` 欄位，等於在 WS5 重犯這件事：兩個 objective 都掛同一個 technique 時，一筆事件
會同時完成兩個，光靠 `event_type` narrowing 救不了（常見情形下兩者的 event_type 也相同）。

**為什麼用 `action_id` 而非其他鍵**：`action_id` 已經是 Core Event 的既有 join key（見
`store/db.py` 的 `core_events_action_idx` 部分索引、`harness/schema.py` 的契約檢查），
不是新發明的關聯方式。

**為什麼 nested 而非扁平 `action_id: str | None`**：WS2 spec §0.2 主張在會被人手寫的地方
預付。`scenarios/<id>/metadata.yaml` 是手寫檔，日後若要加 `lifecycle:`／`event_type:` narrowing，
巢狀結構讓那是 `telemetry_signal:` 底下的加法，不會變成頂層再散一個相關鍵。代價只是一層
YAML 縮排。

**不違反 WS1 §2.1**（"Objective 與 Action Registry 無外鍵"）：`telemetry_signal.action_id`
指向的是**同一份 scenario 檔自己的** `attack_chain`，不是 P2 Postgres 的 Action Registry；
箭頭停在 scenario→scenario，跟既有的 `Hint.objective_id` 同一種手法。

## ② Blue 分數：v1 只回 red

現況：`Objective` 沒有 `team` 欄位，scenario 檔目前無法宣告 Blue objective；`.scratch/ws8-event-control/spec.md`
（2026-08-12，晚於 WS5/WS1 原始 spec）已判定 Blue 沒有個人分數。若現在建 Blue 計分，等於
無中生有一份跟 #65 判決衝突的 scope。

`{"red": {...}}`（不帶 `blue` 鍵）讓 WS1 §1.1 的「非零和」性質**空真**成立 —— 沒有 Blue
分數就不可能被 Red 動作改變。待 WS3 交付 Blue Action 契約時，加 `blue` 鍵是加法，不用改
既有回應形狀。

## ③ 專用 endpoint，不是通用 `/api/actions`

SA §15 有一份 `POST /api/actions` 的草稿，但目前只有兩個 red-side 動詞（submission、hint）。
從兩個例子歸納出一個 discriminated union，是這個 repo已經否決過兩次的模式（WS1 §4、WS2 §3.2：
不為單一樣本預先抽象）。等 WS3 帶來五個 Blue 動詞，`/api/actions` 才有真正的存在理由。

## ④ hint 疊加取最大值

沒有任何 spec 句子決定這件事（`.scratch/ws1-game-design/spec.md` 只寫了單一 hint 的情形）。
取最大值：簡單、永遠不超過 100%、對玩家最容易解釋「用了提示就打對應的折扣，用更貴的提示
以那個為準」。加總需要一個把百分比夾在 100% 的 clamp，那個 clamp 本身就是在藏一個沒講清楚
的模型缺口；乘法複合最精確但對玩家最難解釋為什麼分數是那個數字。

# 4. 影響

- `Objective.telemetry_signal` 是新增可選欄位，`Scenario.model_post_init` 新增三條驗證
  （telemetry 型別必須有、submission 型別不得有、`action_id` 必須存在於同檔 `attack_chain`）。
- `GET /api/score` 的回應形狀只包含 `red`；加 `blue` 是未來的加法變更，不是本 ADR 要處理的。
- `POST /api/submissions`、`POST /api/hints` 是本票新增的兩個 endpoint；`POST /api/actions`
  不在本票範圍。
