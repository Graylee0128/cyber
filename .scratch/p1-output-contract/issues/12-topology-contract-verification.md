# 12 — 拓樸契約與來源歸屬驗收

**What to build:** 把四條跨世代契約變成可重複執行的腳本，而不是一次性的人工確認。以及證明六台 kali 的來源 IP 在事件中真的可以分辨到個別攻擊者。

**Blocked by:** 03（環境另需 Range Infrastructure 就位）

**Status:** ready-for-agent

**測試性質：** 環境斷言。不硬包裝成 TDD。

- [ ] `TARGET → MGMT` 的 `:3100` `:9090` `:4317` 三個 port 通
- [ ] **`MGMT → TARGET` 反向不通** —— 單向性是 response 走 agent pull 的整個理由
- [ ] `RED → MGMT` deny all
- [ ] collector（Alloy／Falco／response agent）全部在 target 側，不在 mgmt 側
- [ ] 六台 kali 各打一次，事件中出現**六個可分辨的 source IP**，非 SNAT 塌成兩個主機 IP
- [ ] 上述全部腳本化，可重複執行

**範圍界線：** 網段建置屬 workstream 6（Range Infrastructure），本票只負責驗收。環境未就位前，可在單平面 docker 環境開發其餘票，本票最後執行。

契約條文見 SA §12.2。
