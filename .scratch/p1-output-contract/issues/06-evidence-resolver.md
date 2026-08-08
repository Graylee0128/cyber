# 06 — Evidence resolver ＋ Alert Record

**What to build:** 給一個 `event_id`，能取回那個事件前後發生了什麼 —— 而且取回的內容依呼叫者身分不同。這是藍紫兩隊調查能力的基礎。

**Blocked by:** 03

**Status:** ready-for-agent

先寫紅燈：

- [ ] `returns_context_window_not_single_line` —— 回傳事件前後的上下文窗，不是孤立一行。分析師要看的是周遭發生什麼
- [ ] `visibility_filter_applied_per_caller` —— 不同身分取得不同內容

再變綠：

- [ ] Alert Record 保有 Grafana rule 名稱、原始 query、threshold、觸發值、labels、`backend`
- [ ] **`backend` 欄位只存在於 Alert Record**，不得出現在 Core Event
- [ ] resolver 以 `event_id` 為唯一輸入，**不接受呼叫端傳入查詢語法**
- [ ] 換掉 Loki 時只需改 resolver 實作，Core Event schema 不動 —— 用一個假 backend 證明這點

**範圍界線：** HTTP endpoint `GET /evidence/{event_id}` 由 P2 的 Evaluation Engine 提供。本票只交付 resolver 與 Alert Record。防火牆政策不變 —— 不新增 `APP → MGMT :3100`。
