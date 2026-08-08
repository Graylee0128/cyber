# 08 — Falco 作為 runtime sensor

**What to build:** exec 進 container 會產生一筆帶 T1059 的 Core Event，而且**當那條 Grafana rule 被停用時，畫面上呈現的是偵測缺口而不是可見性缺口**。

**Blocked by:** 03, 04

**Status:** ready-for-agent

先寫紅燈：

- [ ] `falco_event_reaches_core_event_as_T1059`
- [ ] **`disabled_rule_shows_detection_gap_not_visibility_gap`**

第二條是 [ADR 0001](../../../docs/adr/0001-p1-output-contract.md) ③ 選 D1 的**唯一理由**。它綠了，D1 才算被證明；它紅了，代表我們實質上退回了 D3，而 D3 會讓 Falco 覆蓋範圍內的偵測缺口全部不可觀測。

再變綠：

- [ ] Falco 事件經 Alloy 寫入 Loki，**未直送 Event Service**
- [ ] 由 Grafana rule 決定是否構成告警，Falco 只提供事實
- [ ] Falco 部署在 Z-TARGET 側，不在 Z-MGMT
- [ ] SA §7 Scenario 03（敏感檔存取）亦通

**已知殘留：** 「Falco 根本沒寫那條 rule」的情形，兩種路由都會呈現為可見性缺口。那要靠 rule inventory 補，不在本票範圍。
