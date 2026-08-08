# 05 — Alert lifecycle：resolved 事件

**What to build:** 攻擊停止後系統會說「結束了」。沒有這個，MTTR 與 containment duration 都算不出來。

**Blocked by:** 03

**Status:** ready-for-agent

先寫紅燈：

- [ ] `resolved_shares_event_id_with_firing` —— 同一告警的兩個 lifecycle 共用同一個 `event_id`
- [ ] `pending_produces_no_event` —— Grafana 的 Pending 是內部狀態，不是遊戲語意

再變綠：

- [ ] `lifecycle` 欄位只有 `firing` 與 `resolved` 兩個值
- [ ] `containment duration` 可由兩者時間差算出
- [ ] **`containment duration` 不得命名為 MTTR** —— 它測的是攻擊停了沒，與藍隊做了什麼無關（[ADR 0001](../../../docs/adr/0001-p1-output-contract.md) ⑦）
- [ ] Battleboard timeline 不會出現「快要偵測到了」這類無資訊量的行
