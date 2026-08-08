# 03 — 讓第一條紅燈變綠：SQLi → Core Event

**What to build:** 打一次 SQLi，最後拿到一筆符合契約的 Core Event。建立 webhook receiver，讓 02b 的測試轉綠。

**Blocked by:** 02b

**Status:** ready-for-agent

**設計決定：** receiver 從一開始就寫成 pure core ＋ I/O shell，**不先做壞結構再重構**。沒有測試施力面的話，邏輯會長進 HTTP handler 裡再也拆不出來。

- [ ] 02b 的測試轉綠
- [ ] Receiver 分為 pure core（純函數、無 I/O）與 shell（HTTP、儲存、查詢），core 可獨立單元測試且不需 docker
- [ ] `event_id` 由 receiver 鑄造；**先寫 Alert Record，再發 Core Event**，順序不可顛倒
- [ ] Core Event 落到 P1 自有的持久化儲存 —— Cyber Range Core 屬 workstream 5，目前尚不存在，外送以可插拔 adapter 隔離，日後接上不需改 core
- [ ] **現有 prototype 的 ipset 直寫行為保持可用**（expand，不取代；contract 由 09 執行）
- [ ] Grafana `eval interval` 設為 10s
