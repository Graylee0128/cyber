# 01 — 時間同步基準線

**What to build:** 所有參與遙測的 host 與 container 對到同一時間源，偏差可量測、可重複驗證。這是 prefactor —— 時間不同步，紅隊動作與藍隊偵測關聯不起來，MTTD 與 action coverage 全部失去意義。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**測試性質：** 環境斷言，不是 red-green-refactor。不硬包裝成 TDD。

- [x] 有一個可重複執行的檢查，輸出各節點與基準時間源的偏差 —— `python -m purple.clock.cli`
- [x] 所有節點偏差 < 100ms —— `MAX_SKEW_MS`，設定檔可覆寫
- [x] 檢查失敗時明確指出是哪個節點，而非只回報「失敗」
- [x] 偏差門檻寫成具名常數，日後可調
- [x] 檢查納入後續所有契約測試的前置條件 —— `clock_in_sync` fixture（**fail 不 skip**）

## 實作時多出來的兩個判定

票上沒寫，但不加就會產生假綠燈：

- **`UNCERTAIN`** —— `docker exec date` 的往返時間若與門檻同量級（±round_trip/2 > 門檻），
  量到的偏差是雜訊。此時不回報偏差值，避免一個看似精確的數字誤導人。
- **基準節點讀不到 → 全部節點標 UNREACHABLE** —— 不是只讓基準一個人失敗、
  其餘照舊顯示綠燈。基準不可信時沒有任何偏差是可信的。

## Status: done（2026-08-08）

**已驗證**：41 tests pass；CLI 對正常設定回 0、對讀不到的節點回 1 並指名 `ghost`、
對缺檔回 2。

**未驗證**：`read_docker` 沒有對真實容器跑過 —— 本機 docker CLI 不在 PATH，
且 Z-TARGET／Z-MGMT 尚未建置。[config/clock-nodes.yaml](../../../config/clock-nodes.yaml)
的 docker 節點全部註解著，環境就位後取消註解並在 12 一併驗收。
