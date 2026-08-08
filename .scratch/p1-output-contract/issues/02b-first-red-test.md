# 02b — 第一條紅燈

**What to build:** 把 spec 的核心契約寫成一條端到端測試。**本票結束時測試是紅的，這是正確結果。** 這一票把規格變成可執行的斷言。

**Blocked by:** 02a

**Status:** ready-for-agent

- [ ] 測試名稱表達契約本身：注入 SQLi 後應出現一筆符合 schema 的 Core Event
- [ ] 斷言 Core Event **不含** `evidence_ref`，且全文無 `loki` / `logql` / `promql` 字樣
- [ ] 斷言必要欄位齊全：`event_id` `exercise_id` `scenario_id` `event_type` `lifecycle` `technique` `visibility` `observed_at` `source` `team` `target`
- [ ] 執行後得到紅燈，且失敗訊息說明「沒有收到事件」，而非載具自身錯誤
- [ ] **不實作任何 receiver 邏輯** —— 讓它紅著交票
