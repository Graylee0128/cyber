# 07 — Source Registry 狀態機

**What to build:** 系統能分辨「來源有裝但沒收到」與「來源根本沒裝」。把兩者混為一談，會系統性地冤枉藍隊。

**Blocked by:** 03

**Status:** ready-for-agent

**測試性質：真 TDD。** 狀態機是純函數。

先寫紅燈：

- [ ] **`expired_heartbeat_is_stale_not_absent`** —— 這一條就是整張票的理由
- [ ] `heartbeat_within_tolerance_is_healthy`
- [ ] `not_expected_is_absent`

再變綠：

- [ ] `expected` 清單來自 scenario 定義，**不從部署狀態推導** —— 掉線的來源會從推導結果消失，被洗成「不在範圍內」
- [ ] heartbeat 30s 間隔 / 90s 容忍，值為具名常數
- [ ] 可查詢任一來源的 `stale` 區間，P2 據此自動判 `unknown` 並填入原因
- [ ] 狀態機為純函數，可不啟 docker 單元測試
- [ ] 實測：殺掉 Falco 後狀態轉 `stale`，**不是** `absent`

理由見 [ADR 0001](../../../docs/adr/0001-p1-output-contract.md) ⑧ ⑫。
