# Spec — Product UI（WS7）視覺提案總覽

- **Status**: draft，僅供討論；**擁有者是 WS7 Product UI**（README：⬜ 未開始，理由是「要顯示的數字由 4-P2／5 產生，不宜早做」）——本檔與底下三份 demo 是視覺提案，不代表 WS7 提前開工
- **上位文件**：[SA §5.1 Player Portal](../../資安攻防平台_系統架構設計文件_v0.1.md)／§5.2 Blue SOC Console／§5.5 Instructor Console
- **相關但不在本檔**：Battleboard（SA §5.4）demo 在 [.scratch/battleboard-ui/](../battleboard-ui/spec.md)；Purple Console（屬 WS4-P2，非 WS7）demo 在 [.scratch/purple-console-ui/](../purple-console-ui/spec.md)

WS7 一共四個畫面，audience 完全不同，這是設計時最容易踩的坑——同一份資料，不同角色看到的深淺與遮蔽完全不同：

| 畫面 | Audience | 看得到 | 看不到 |
|---|---|---|---|
| **Player Portal** | Red、Blue | 自己的 Mission／Objective／Score／Hint／Flag 提交 | Detection Rule、Threshold、Falco Rule、Ban TTL（SA §5.1 明訂 Red 不可見）|
| **Blue SOC Console** | Blue、部分 Purple | Alert、Incident、Evidence、Source/Target IP、Timeline、Response Action（藍隊自己的作戰細節，可看 raw） | 其他隊伍的資料 |
| **Battleboard** | Red／Blue／Purple／Instructor／教室大螢幕 | Sanitized Event、狀態（○／🟡／🔴／🟢）、比分 | Rule threshold、Raw payload、Detection query、Ban TTL、Internal IP（SA §5.4）|
| **Instructor Console** | Instructor only | 以上全部 + Raw Event + Override 權限 | （教官是唯一全知角色）|

**核心原則**：越往下（Player → Blue SOC → Instructor）看到的細節越多；Battleboard 是唯一的「公開層」，deliberately 資訊量最少。四個畫面不能互抄彼此的呈現方式（沿用 [purple_platform_plan.md §3.9](../../purple_platform_plan.md) 對 Console vs Battleboard 的判準，同樣適用在這裡）。

---

# 待討論

## Q1. Player Portal 的 Hint 扣分怎麼呈現，才不會變成「不用白不用」？

[WS1 spec](../ws1-game-design/spec.md) 已定案 hint 會扣分，但 UI 上「要不要顯示扣了多少分再讓玩家確認」還沒決定——顯示太清楚可能鼓勵玩家精算，不顯示又不透明。

## Q2. Blue SOC Console 要包多少層在 Grafana 之上？

SA §5.2：「底層可用 Grafana，但產品 UI 可再包一層簡化的 Incident UX」——包到什麼程度沒定案：只是換皮（iframe/連結出去），還是真的做一個獨立的 incident 佇列 UI？後者工程量差很多。

## Q3. Instructor 的 Override Score／Inject Event 需不需要留操作紀錄？

Instructor 可以覆寫分數、注入事件——這兩個操作如果演練結束後要寫 Exercise Report（[#28](https://github.com/Graylee0128/cyber/issues/28)），報告該不該標註「這個分數是教官手動調整過的」，否則報告的可信度會被質疑。

---

# Demo

- [player-portal.html](./player-portal.html) — 紅藍隊玩家視角
- [blue-soc.html](./blue-soc.html) — 藍隊事件處置台
- [instructor-console.html](./instructor-console.html) — 教官控台

三份都是零依賴單檔 HTML、資料寫死，不吃真實 API，只是討論用的視覺提案。
