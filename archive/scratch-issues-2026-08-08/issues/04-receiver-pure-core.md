# 04 — Receiver pure core：欄位治理

**What to build:** Core Event 的欄位在離開 receiver 之前就被治理好 —— 不合法的 technique 進不來，rule 作者無法自行決定誰看得到什麼。

**Blocked by:** 03

**Status:** ready-for-agent

**測試性質：真 TDD。** 這一票全是純函數，紅燈先寫，秒級完成，不需啟動 docker。

先寫這四條紅燈：

- [ ] `technique_outside_whitelist_rejected` —— 白名單外的值被拒收**並記錄**，不得靜默通過
- [ ] `rule_cannot_override_visibility` —— Grafana rule 標的 visibility 無效，一律以 `event_type` 對照表為準
- [ ] `alert_record_written_before_core_event` —— 順序不可顛倒，`event_id` 只有一個產生來源
- [ ] `core_event_contains_no_backend_fields` —— 全文無 `loki` / `logql` / `promql` / `backend`

再讓它們變綠：

- [ ] technique 白名單建立為單一檔案，Grafana rule label 與紅隊動作註冊清單**共用同一份**
- [ ] 白名單層級統一 —— 不可一邊 `T1190` 一邊 `T1190.001`，否則 coverage 表永遠對不起來
- [ ] 白名單支援 `note` 欄位承載判讀限制（如 T1110 需與成功登入證據連結）
- [ ] `event_type → visibility` 對照表覆蓋 spec 列出的所有 event_type
- [ ] 上述測試全部不需啟動 docker

理由見 [ADR 0001](../../../docs/adr/0001-p1-output-contract.md) ⑤ ⑪。
