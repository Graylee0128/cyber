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

## Q1. Player Portal 的 Hint 扣分怎麼呈現，才不會變成「不用白不用」？ —— **已答（2026-08-13）**

[WS1 spec](../ws1-game-design/spec.md) 已定案 hint 會扣分，但 UI 上「要不要顯示扣了多少分再讓玩家確認」還沒決定——顯示太清楚可能鼓勵玩家精算，不顯示又不透明。

**答**：確認前顯示扣分金額（按 hint 前跳「扣 X 分，確定要看嗎？」）。不顯示等於暗坑，容易變投訴來源；顯示不等於鼓勵精算，精算本來就是玩家的策略選擇。

## Q2. Blue SOC Console 要包多少層在 Grafana 之上？ —— **已答（2026-08-13）**

SA §5.2：「底層可用 Grafana，但產品 UI 可再包一層簡化的 Incident UX」——包到什麼程度沒定案：只是換皮（iframe/連結出去），還是真的做一個獨立的 incident 佇列 UI？後者工程量差很多。

**答**：純換皮／iframe 嵌 Grafana，包一層導覽／認證即可，不重做獨立 incident 佇列 UI。SA §5.2 原文只要求「可再包一層簡化 UX」，沒說要重做；WS7 現在受限於 #21/#26/#32/#33 都還沒出真數字，先上輕量版，真正需要獨立 UI 時再升級不算浪費前期投入。

## Q3. Instructor 的 Override Score／Inject Event 需不需要留操作紀錄？ —— **已答（2026-08-13）**

Instructor 可以覆寫分數、注入事件——這兩個操作如果演練結束後要寫 Exercise Report（[#28](https://github.com/Graylee0128/cyber/issues/28)），報告該不該標註「這個分數是教官手動調整過的」，否則報告的可信度會被質疑。

**答**：要留稽核，且與 [#55](https://github.com/Graylee0128/cyber/issues/55)（Instructor Override Score／Inject Event 稽核）統一管道，不各做一套——這個原則已隱含在 [#65](https://github.com/Graylee0128/cyber/issues/65) decision #24。本題剩下的部分是 Exercise Report（#28）要不要標註「此分數被人工調整過」：要標註，否則報告可信度會被質疑。

## Q4.（2026-08-12 補）Player Portal 要不要嵌入即時 Shell？——組員已定方向：要，嵌入 web page

SA §5.1 的 Player Portal 功能清單（Login/Mission/Target/Difficulty/Hint/Objective/Score/RemainingTime/FlagSubmission）
裡沒有「Shell」——原本假設玩家操作是走外部 SSH／VNC 直連自己的 kali VM，Portal 只是任務資訊 HUD。**組員已表態方向：
shell 要嵌入這個 web page**（demo 見 [player-portal.html](./player-portal.html) 的終端機面板），不是外部 SSH／VNC。

這個決定本身不難，難的是三個隨之而來的子問題，**尚未定案**：

1. **連線隔離**——每個 player 的 WebSocket session 只能連到自己的 kali VM，不能連到別人的，怎麼保證
2. **要不要側錄操作紀錄**——跟 Q3 的 Instructor 稽核是同一類問題，若要留，格式跟 Q3 能不能共用
3. **這條連線走哪個網段**——是走 Z-APP（Player Portal 所在區）還是直接連 Z-RED，涉及 README 網段表既有的防火牆規則要不要為此新開一條

**技術選型 —— 已答（2026-08-13）**：xterm.js + PTY over WebSocket。這是業界標準組合，WS8 的 ttyd 本身就是這個模式，兩邊技術棧天然對齊。

## Q5.（2026-08-12 補）Blue 的 Player Portal 頁面內容很薄，要不要乾脆不做，直接導去 Blue SOC Console？

[WS1 spec §1.3](../ws1-game-design/spec.md) 已定案「Blue 側不做個人化，P2 現有 KPI 本來就是全場級指標」——這代表 Blue
版的 Player Portal 天生沒有 Objective／Hint／Flag 可放（[demo 的 Blue 分頁](./player-portal.html) 只剩 Mission 資訊
＋團隊 KPI），跟內容豐富的 Red 版形成明顯落差。這不是 demo 沒做完，是設計上的結構性差異。待討論：這頁值不值得獨立存在，
還是 Blue 登入後直接導向 [Blue SOC Console](./blue-soc.html)（那才是 Blue 真正的操作畫面）？

---

# Demo

- [player-portal.html](./player-portal.html) — 紅藍隊玩家視角
- [blue-soc.html](./blue-soc.html) — 藍隊事件處置台
- [instructor-console.html](./instructor-console.html) — 教官控台

三份都是零依賴單檔 HTML、資料寫死，不吃真實 API，只是討論用的視覺提案。
