# 01 — 時間同步基準線

**What to build:** 所有參與遙測的 host 與 container 對到同一時間源，偏差可量測、可重複驗證。這是 prefactor —— 時間不同步，紅隊動作與藍隊偵測關聯不起來，MTTD 與 action coverage 全部失去意義。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**測試性質：** 環境斷言，不是 red-green-refactor。不硬包裝成 TDD。

- [ ] 有一個可重複執行的檢查，輸出各節點與基準時間源的偏差
- [ ] 所有節點偏差 < 100ms
- [ ] 檢查失敗時明確指出是哪個節點，而非只回報「失敗」
- [ ] 偏差門檻寫成具名常數，日後可調
- [ ] 檢查納入後續所有契約測試的前置條件
