# 02a — 測試載具

**What to build:** 一組讓契約測試寫得出來的工具：注入攻擊、捕捉事件、對事件下斷言。**本票不實作任何 pipeline 功能** —— 它只是讓後面的紅燈有地方可寫。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 可用程式碼觸發一次 SQLi 攻擊，不靠人手打 curl
- [ ] 可捕捉 pipeline 產出的 Core Event，並在測試中取用
- [ ] 提供斷言輔助：欄位比對、schema 合規、等待事件出現（含 timeout 與逾時訊息）
- [ ] 載具在本機與 CI 以同一指令啟動
- [ ] **載具自身有 smoke test，證明它會在事件沒出現時失敗**

最後一條不可省：測試載具最常見的缺陷是永遠綠。一個從不會紅的載具，比沒有載具更糟。

## 本票同時拍板的實作選型

語言已定為 **Python**（見 map.md Decisions）。以下四項在做這張票時一次決定，結論回寫 map.md，**不另寫 spec** —— 是實作選型不是契約：

- [x] 測試框架與執行指令 —— **票 01 已定**：pytest ＋ `src/` layout，`python -m pytest`。本票只需沿用
- [ ] 本機環境起法（`docker-compose.yml` 落在哪、起哪些服務）
- [ ] **venv 策略** —— 票 01 用 `pip install -e .` 裝進 user site-packages，沒有 venv。這是暫時的，本票要定
- [ ] Core Event 的「自有儲存」是什麼（檔案／SQLite／Postgres）—— 03 依賴此決定
- [ ] Receiver 的 HTTP 框架與 port
