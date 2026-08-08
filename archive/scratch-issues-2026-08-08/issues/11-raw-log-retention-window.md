# 11 — raw log 保留時段

**What to build:** 演練期間可以查到「沒有觸發任何規則」的原始 log，時段外則不保留。這是偵測缺口分類的前提 —— 沒有 raw，就沒辦法證明「有 log 但沒規則」。

**Blocked by:** 03

**Status:** ready-for-agent

先寫紅燈：

- [ ] `raw_available_inside_window`
- [ ] `raw_absent_outside_window`

再變綠：

- [ ] 開窗＝演練開始前 10 分鐘，關窗＝結束後 30 分鐘，自動切換不需人工
- [ ] 時段內可查到未觸發任何規則的原始 log
- [ ] 磁碟用量可量測，記錄一次完整演練的實際值
- [ ] Exercise Report 產出時把引用到的證據**快照進報告本身**，使 retention 過期後報告仍可讀

策略與理由見 [ADR 0001](../../../docs/adr/0001-p1-output-contract.md) ⑩。
