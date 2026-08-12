# Spec — Purple Console UI（畫面一／畫面二／Exercise Report）

- **Status**: draft（討論用，尚未 grilling 定案；定案後比照 P1/WS5 遷出至 `docs/`，見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **範圍**：涵蓋 [#26](https://github.com/Graylee0128/cyber/issues/26)（畫面一：Coverage 表）、[#27](https://github.com/Graylee0128/cyber/issues/27)（畫面二：technique 下鑽）、[#28](https://github.com/Graylee0128/cyber/issues/28)（Exercise Report，收尾快照，非 Console 即時畫面）的**視覺與互動決策**，不重複票裡已經定案的資料規則
- **範圍外**：Battleboard——明確不是紫隊的畫面（§3.9），屬另一個 Product UI workstream，不在本檔討論範圍
- **上位文件**：[purple_platform_plan.md §3.7–3.9](../../purple_platform_plan.md)（兩畫面的內容規格、`⏳`／`❌`／`—` 三態語意、Console vs Battleboard 邊界已在此定案，不在本檔重開）、[discuss.md](../../discuss.md)（Purple Analysis Mode 原始設計來源）
- **現況確認**：`src/` 底下目前只有 `src/purple/`（純 Python 後端）與 `src/range_core/`，**沒有任何前端專案骨架**——本檔的討論結果會是這個專案第一個前端技術決策，不是延用既有慣例

---

# 待決問題（帶去跟組員討論用）

## Q1. 三態視覺編碼：圖示＋顏色，還是純文字徽章？

`✅ Detected` / `❌ Missed` / `⏳ unknown` / `—`（未部署）四種狀態要同時面對「分析師快速掃視」
和「投影／螢幕分享時的可辨識度」。純顏色（紅綠）在色盲情境下會失效；純文字在密集表格裡會拖慢掃視速度。

- **選項 A**：icon + 文字雙重編碼（例如 `✅ Detected`），色盲安全，但表格變寬
- **選項 B**：純色塊徽章（背景色 + 文字），資訊密度高，但需要額外做色盲檢查
- **決策記錄**：（討論後填）

## Q2. 畫面一 → 畫面二 的下鑽是換頁還是原地展開？

[#27](https://github.com/Graylee0128/cyber/issues/27) 描述「點畫面一的某個 technique 進去」，但沒定「進去」的形式。

- **選項 A**：獨立路由／頁面（`/console/technique/T1059`），可分享連結、瀏覽器返回鍵可用
- **選項 B**：同頁展開／modal，不換頁，但無法分享特定 technique 的連結
- **決策記錄**：（討論後填）

## Q3. `config/techniques.yaml` 的判讀限制怎麼呈現？

[#26](https://github.com/Graylee0128/cyber/issues/26) acceptance criteria 要求「每個 technique 顯示判讀限制」
（例如 T1005「僅代表敏感檔被開啟，未證明內容外流」）。這段文字不短，塞進表格會撐開列高。

- **選項 A**：常駐顯示在該列下方（一律可見，但表格變高）
- **選項 B**：hover / 點擊才展開的 tooltip（表格緊湊，但分析師可能漏看限制說明——而限制說明的目的就是防止誤讀，藏起來有違初衷）
- **決策記錄**：（討論後填）

## Q4. 前端技術骨架：先用零依賴靜態頁，還是現在就選框架？

專案目前沒有任何前端程式碼。Purple Console 的 audience 是分析師／Instructor（§3.9），
不是給紅隊或非技術觀眾看的公開畫面，複雜互動需求不高（一張表 + 一個下鑽頁）。

- **選項 A**：先用零依賴的靜態 HTML/CSS/少量 JS 出 MVP，日後有需要再抽框架
- **選項 B**：一開始就選定框架（React/Vue/…），對齊團隊其他前端（若有）的技術棧
- **決策記錄**：（討論後填）

## Q5. Coverage 表的列數上限與捲動行為

畫面一只列本 scenario 涉及的 technique（不放完整 Enterprise Matrix，§3.7 已定案），
但單一 scenario 仍可能有十幾個 technique。

- **選項 A**：固定高度、表格內捲動，維持一屏可見
- **選項 B**：不限高度、整頁捲動
- **決策記錄**：（討論後填）

---

# 已定案、不重複討論的事（引用而非重寫）

- Console 只有兩個即時畫面，第二個是第一個的下鑽（§3.7）——**Exercise Report 不算第三個 Console 畫面**，它是演練結束才產出的快照（§3.8／[#28](https://github.com/Graylee0128/cyber/issues/28)），資料快照後不隨後續變動
- `⏳` = `unknown`，不進涵蓋率分母（§3.5／§3.7）
- Telemetry 欄的 `❌`（有部署沒事件）與 `—`（未部署）不可混用，來源清單須動態產生，不可寫死（§3.7 坑／[#27](https://github.com/Graylee0128/cyber/issues/27)）
- Console 只吃 Evaluation API，無 raw query 權，部署在 Z-APP（§4.1／[#26](https://github.com/Graylee0128/cyber/issues/26)）
- Console 不是 Battleboard，不用管非技術觀眾的可讀性標準；Battleboard 也不是紫隊要交付的畫面（§3.9）
- Exercise Report 三個不可省欄位：告警總量、缺口分類（偵測缺口／可見性缺口）、`unknown` 數量與原因（§3.8／[#28](https://github.com/Graylee0128/cyber/issues/28)）

---

# Demo

配套的可點擊 demo 在 [demo.html](./demo.html)——零依賴單檔 HTML，含三個分頁：畫面一／畫面二／Exercise Report，
用 §3.7、§3.8 範例資料做出來，只是討論用的視覺提案，**不是** [#26](https://github.com/Graylee0128/cyber/issues/26)／[#27](https://github.com/Graylee0128/cyber/issues/27)／[#28](https://github.com/Graylee0128/cyber/issues/28) 的實作
（不吃真實 Evaluation API／Evaluation 快照，來源清單與報告內容是寫死的示範資料，不代表 Q4 已定案）。
