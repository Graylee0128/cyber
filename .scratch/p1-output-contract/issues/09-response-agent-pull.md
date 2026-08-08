# 09 — Response 閉環：agent pull ＋ ipset

**What to build:** 攻擊 → 自動封鎖 → 連線 timeout，全程可驗，並產生可算出 MTTR 的事件。派送必須以 agent 主動拉取完成，才不會破壞 `TARGET → MGMT` 單向。

**Blocked by:** 03

**Status:** ready-for-agent

先寫紅燈：

- [ ] `attack_leads_to_block_and_response_event`
- [ ] **`agent_pulls_no_inbound_to_target`** —— 全程 MGMT／APP 不對 target 建立任何連入連線

再變綠：

- [ ] `response.executed` 事件產生，**MTTR 的終點是 ipset 寫入成功**，不是 Grafana Resolved
- [ ] response 失敗時產生 `response.failed`，`visibility` 為 `purple`
- [ ] **移除 03 保留的 ipset 直寫路徑**（expand–contract 的 contract 階段）
- [ ] 移除後 03 與 08 的測試仍全綠

MTTR 定義見 [ADR 0001](../../../docs/adr/0001-p1-output-contract.md) ⑦。

**語言代價（2026-08-08）：** agent 用 Python，不是 Go。target 側因此需要 Python runtime 或打包步驟（PyInstaller 之類）。做本票時決定走哪條，結論回寫 map.md。單一語言的好處是 02a 的載具只顧一套；代價落在這裡。
