# Spec — Battleboard（討論用 mock，非本次開工票）

- **Status**: draft，僅供討論；**擁有者是 WS7 Product UI，不是紫隊（WS4-P2）**
- **README 現況**：WS7 標示 ⬜ 未開始，理由是「要顯示的數字由 4-P2／5 產生，不宜早做」——本檔和 demo 只是**視覺提案**，不代表 WS7 提前開工，也不吃任何真實 API
- **上位文件**：[SA §5.4 Live Battleboard](../../資安攻防平台_系統架構設計文件_v0.1.md#54-live-battleboard)、[purple_platform_plan.md §3.9](../../purple_platform_plan.md)（Console vs Battleboard 的邊界判準）、[discuss.md](../../discuss.md)（原始戰況版設計討論）
- **為什麼現在做這份 mock**：在討論 [Purple Console UI](../purple-console-ui/spec.md) 時，組員想看到 Battleboard 長什麼樣以理解兩者邊界——這是**邊界示意**，不是要紫隊或任何人現在就去實作 WS7

---

# 已定案、直接引用（不重複討論）

- **Audience**：Red、Blue、Purple、Instructor、教室大螢幕（SA §5.4）——公開介面，不是操作介面
- **8 個 widget**（SA §5.4）：Match Header／Red-Blue Score／Round Timer／Attack Chain Progress／Scenario-based MITRE ATT&CK／Defense Status／Live Timeline／Objective Progress
- **不得公開**：Rule threshold、Raw payload、Detection query、Secret、Ban TTL、Internal IP mapping、Falco rule detail（SA §5.4）
- **只展示 Sanitized Event**：`Raw Security Event → Normalization → Sanitization → Public Battle Event`（SA §5.4）
- **公開層只能用「狀態」，不能用「裸比率」**（purple_platform_plan.md §3.9）：

  | 形式 | 適合放哪 | 為什麼 |
  |---|---|---|
  | 攻擊鏈進度 ○／🟡／🔴／🟢 | Battleboard | 狀態，不需要分母 |
  | `8 / 10 技法`、`Detected 3/4` | Battleboard 可 | 分數形式，分母看得見 |
  | 裸百分比（`82%`） | 只放 Console | 藏起分母，投影幕上比表格裡更難被質疑 |

  本檔 demo 的 Defense Status 因此改成計數（`Detected 3/4`、`Blocked 12`），沒有沿用 discuss.md 舊草稿裡的 `Detection 82%`。

---

# 待討論（如果組員想聊 WS7，不是本次強制要定案）

## Q1. 這份 mock 現在的用途，是「理解邊界」還是「順便定案 WS7 視覺」？

如果只是前者，討論完不用填決策記錄；如果組員想順便定案，建議另外開一份跟 WS7 對齊的 spec（本檔目前是為了陪 Purple Console 討論而生，範圍是邊界示意）。

## Q2. Sanitization 規則由誰審？ —— **已拍板（2026-08-13，見 #82）**

`purple_platform_plan.md §3.9` 提到「sanitization 邊界也該由第三方審核，不能自己審自己」——WS7 做畫面，但過濾規則審核者是誰，目前沒有答案。

**產生規則本身（誰寫、輸出什麼形狀）已切為 [#82](https://github.com/Graylee0128/cyber/issues/82)**。

**決策：沿用專案既有 agent workflow（#67）—— 紫隊／P2（Codex）交付規則實作，Claude 在 CI 綠後做獨立 architecture review。**
寫規則的人與審的人不同，已滿足「不能自審」；不額外加一層人工複核。

## Q3. 技法要不要匿名化為 `Attack #N`，不直接秀 MITRE 編號？ —— **已拍板（2026-08-13，見 #82）**

組員提出：同一個 scenario 通常會拿給下一組隊伍重打，Battleboard 若直接顯示 `T1059` 這種可查的公開編號，
等於提前公布「這關考什麼」，對還沒打過這關的隊伍是洩題。

**決策：是，匿名化。** 公開層一律顯示 `Attack #1`／`#2`…，真實 MITRE ID 只留在賽後 Exercise Report、
或 Purple／Instructor 這種非公開畫面。demo 目前的 strawman 做法（見下方 Demo 段落）即為定案版本，不需再改。

## Q4.（比 Q3 更根本）Red／Blue 得手或被偵測的即時狀態，要不要延遲揭露？ —— **已拍板（2026-08-13，見 #82）**

匿名化只解決「洩漏技法類別」，解決不了另一件事：`🔴 Attack #2 紅隊得手` 這種即時狀態本身，就是在藍隊自己的 SOC
還沒告訴他們之前，把「有沒有被打穿」的答案現場公布出來——等於考試中把答案寫在黑板上，讓 Battleboard 變成藍隊的外掛，
汙染正在被量測的 MTTD 指標。

**決策：(b) 即時只給 Instructor，公開層維持模糊。**

- 保留 Battleboard 的即時戰況感——計時器、Objective 進度、比分等照常即時更新。
- 但「這關有沒有被偵測到／被打穿」這個具體訊號，公開層（Red／Blue／教室大螢幕）在揭露前只顯示中性狀態（例如「進行中」），
  不呈現 hit／miss／detected 的具體結果；Instructor 畫面（非公開）可即時看到完整狀態。
- schema 上「延遲中」與「已揭露」須是可區分的兩種狀態（見 #82 acceptance criteria），揭露時機由後續實作決定
  （例如回合結束、或 Instructor 手動觸發）。

---

# Demo

[demo.html](./demo.html)——零依賴單檔 HTML，戰況大螢幕風格（非 Purple Console 的分析師風格），
資料寫死，技法已匿名化為 `Attack #N`（見 Q3）。目的是讓組員一眼看出「Battleboard 看到的東西」跟
「[Purple Console](../purple-console-ui/demo.html) 看到的東西」有什麼不同——同一場演練、同一個技法，
Console 敢顯示真實 MITRE 編號、Telemetry 來源與規則命中細節，Battleboard 連編號都不敢秀真的，只敢顯示匿名化後的狀態。
