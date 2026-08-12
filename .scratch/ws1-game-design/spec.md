# Spec — WS1 Product / Game Design

- **Status**: draft（尚無票消費，定案後比照 P1 遷出至 `docs/`；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-11 grilling session（八題逐一確認，本檔為落地記錄）
- **上位文件**：[資安攻防平台_系統架構設計文件_v0.1.md](../../資安攻防平台_系統架構設計文件_v0.1.md) §4、§9、§10、§11、§18；[purple_platform_plan.md](../../purple_platform_plan.md)

WS1 是 SA §4.1 定義的「隱形阻塞」——它不寫程式，但 WS5（Exercise State／Score／API）與
WS7（四種 Console）都由它定義。本檔把八個原本分散在 SA §9／§10／§18 與
`purple_platform_plan.md` §7.2 的「待決」問題逐一拍板，讓下游不必用猜的規格寫實作。

---

# 1. 計分模型

## 1.1 Red／Blue 各自獨立評分，非零和對戰

Red 與 Blue 各自有一份獨立的 Objective-based Score（草案見 discuss.md、SA §9），**不互相扣減**。
沒有「贏家」，只有兩份成績單。

**為什麼不是零和**：WS4-P2 已經蓋好的 `action coverage`／`confirmation rate`／MTTD／MTTR
全部是「評估」語言，不是「對戰」語言——是紫隊獨立判讀紅藍雙方表現的 KPI，不是「Blue 偵測到
就扣 Red 分」。零和模型會要求 P2 的偵測事件即時觸發 Red 扣分，等於讓 P2 的產出被 WS1 的計分
邏輯綁架，違反 [plan §1.1](../../purple_platform_plan.md) 的切分原則。

Exercise Report（P2-8／#28）呈現方式維持並列陳述：`Red Attack Success 67% ／ Objectives 4/7`
與 `Blue action coverage 82%` 並排，不合併成單一勝負判定。

## 1.2 Purple 不參與計分，只出 KPI

Purple 的 coverage／MTTD／MTTR／confirmation rate 不轉譯成分數，不上 Battleboard，
只留在 Purple Console 與 Exercise Report。

**為什麼**：[plan §3.9](../../purple_platform_plan.md) 已定案「Battleboard 不提供裸百分比給公開層」——
`action coverage 82%` 這種比率只能放 Console。這條規則存在的理由本身就是「Purple 的產出是
分析用 KPI，不是拿來比賽的分數」。若要轉譯成分數上公開層，需要額外定義一套轉譯規則，
現在沒有任何票或文件想過這件事，不做。

## 1.3 計分粒度：Red 分個人，Blue 有身分不計分，Action Registry 維持隊級

Red 的 Objective／Score 記錄到**個人**（對應 Z-RED 六台 kali 各自獨立、不可 SNAT 塌縮的來源 IP，
見 [README 網段速查](../../README.md)），團隊分數＝個人分數加總。

P2 的 Action Registry（#21 起）**不受影響，維持隊級**，不記錄是哪個 kali 做的——藍隊的偵測
能力跟「是誰打的」無關，拆成六份只會讓涵蓋率分母碎片化。

**影響**：WS5 的 Exercise State schema 中，Objective 完成記錄需多一個 `player_id`／`source_ip`
欄位；P2 現有及規劃中的票（#21–#28）不需要改動。

### Blue 側：有 `player_id`，但不轉個人分數（2026-08-12 #65 修訂）

> **原文**：「Blue 側不做個人化——P2 現有的 KPI 本來就是全場級指標，沒有『哪個分析師』的
> 概念，維持現狀。」
> **修訂原因**：[WS8 spec §6](../ws8-event-control/spec.md) 拍板藍隊拿受限 shell ＋ 每人一段
> Z-BLUE 之後，「藍隊沒有自己的機器」這個前提不再成立。

拆成兩件事看：

| | 定案 | 為什麼 |
|---|---|---|
| 藍隊有沒有 `player_id` | **有** | 每人一段機器，不綁人就不知道 shell 該接到哪台。這是座位綁定的必然結果，不是計分選擇 |
| 藍隊有沒有個人**分數** | **沒有** | 見下 |
| Action Registry 粒度 | **維持隊級** | 不受影響，理由同上一段 |

**為什麼有 `player_id` 卻不給個人分數**：紅藍的分數有結構性不對稱。
紅隊分數反映「自己做了什麼」（拿到 flag），完全不動作就是 0 分；
藍隊的「守住」反映的是「對手沒做到什麼」，完全不動作會**自動滿分**。

紅隊打哪一段是自由的（§3 無指派目標），50+ 規模下攻擊分布必然不均 ——
沒被打的藍隊員白拿滿分，被三個人圍毆的拿 0。這不是難平衡，是分數與能力無關。

**因此藍隊 Portal 顯示的是「我這段」的狀態**（DMZ 是否失守、flag 是否還在），
不是分數，也不上 Battleboard 排行。藍隊真正屬於自己行為的部分（investigation、判讀、
封鎖建議）由 [WS3](../ws3-blue-ops/spec.md) 與 P2 Action Registry 以**隊級**評量。

**若日後要補藍隊個人分**，方向是「只計主動動作、不計守住與否」，
不是把守住轉成分數 —— 後者會把上面那個不對稱直接寫進計分公式。

---

# 2. Objective

## 2.1 與 P2 Action Registry 完全無關，無外鍵

Objective（WS1，敘事層，如「Capture Flag」）與 Action Registry（P2／#21，技術層，
ATT&CK technique 清單）是兩份獨立的東西，**不建立關聯欄位**。

**為什麼**：兩者評的是不同對象——Objective 評紅隊進度，Action Registry 評藍隊有沒有偵測到。
[plan §1.1](../../purple_platform_plan.md) 已明講 Evaluation 的分母定義權要留在紫隊手上；
若 Action Registry 依賴 Objective 定義，P2 的分母敘事會被迫跟著 WS1 未來的改動漂移。
且兩者判定機制本來就不同源（見 §2.2）。

**直接後果**：#21 現在可以照原 schema 開工，不需要為 WS1 預留欄位。

## 2.2 完成判定：混合制

| Objective 類型 | 判定方式 | 範例 |
|---|---|---|
| 技術類 | Core Event 遙測自動判定 | `discover_endpoint`、`trigger_sqli`——已有現成 P1 偵測路徑 |
| 證明類 | 玩家主動提交比對 | `capture_flag`——遙測只能證明「敏感檔被開啟」，證明不了玩家真的拿到內容 |

**為什麼混合而非單一模式**：`config/techniques.yaml` 裡 T1005 已有判讀限制「僅代表敏感檔被
開啟，未證明內容外流」——這條限制本身就劃出了遙測能力的邊界，Capture Flag 這類 objective
正好落在這個邊界之外，只能靠玩家提交解決。技術類 objective 若也要求提交，等於浪費已經蓋好
的 P1 偵測管線、增加不必要的操作摩擦。

**直接後果（非新決策，是 §2.1＋本節的必然推論）**：WS5 需要自建一套輕量的「Objective 是否
達成」判定邏輯（查詢 Core Event 是否存在符合條件的事件，或比對玩家提交），**不能借用 P2 的
hit/miss/unknown 分類引擎**——那套引擎語意是「藍隊有沒有偵測到」，跟「紅隊進度完成沒」不同。

---

# 3. Hint

Hint 扣分——使用一次，該 objective 的分數打折（例如 Capture Flag 原本 +500，用過 hint 後
拿 +250；確切折扣比例由各 scenario 作者決定，見 §4）。

**為什麼扣分**：README 開宗明義「可計分」——若 Hint 完全免費，分數會失去鑑別力（硬幹拿滿分
與開好開滿拿滿分無法區分）。這也是資安圈玩家最熟悉的 CTF 慣例，零學習成本。

---

# 4. Difficulty

**純內容標籤，無平台公式。** `difficulty: easy／medium／hard`（SA §11 草稿已有此欄位）只是
給玩家篩選關卡用的分類標籤。Hint 扣分比例、Source Registry 完整度（哪些遙測來源 `expected`）、
攻擊鏈長度，全部由該 scenario 的作者在 Scenario Package 裡自行決定，平台不強制任何對應公式。

**為什麼現在不訂公式**：目前連一個 Scenario Package 都還沒做出來（WS2 未開始），在零個實例
的情況下訂公式，訂的是空氣。等真的做出 3–5 個 scenario 後，再回頭抽公式，抽出來的東西才是
真的、不是猜的。此欄位本身不用改，只是現在沒有平台強制力——是可逆的選擇。

**對 §18 原問題的具體回應**：「Difficulty 如何影響 Detection」不需要平台層回答——若某個 hard
scenario 的作者想讓藍隊在能見度不足下作戰，直接在該 scenario 的 Source Registry 定義裡少放
幾個 `expected` 來源即可，不需要「difficulty=hard 系統自動拿掉 telemetry」這種隱藏規則。

---

# 5. Exercise 生命週期

## 5.1 結束條件：純時間制

固定 `duration`（SA §11 草稿：`duration: 30m`），時間到就結束，**不因 Red 完成度提早結束**。

**為什麼不提早結束**：[plan §3.2](../../purple_platform_plan.md) 強調 MTTD 等指標「所有
scenario 一致，可比」——這個可比性前提是每場演練的觀察窗口長度一樣。若 Red 完成即提早結束，
同一 scenario 兩場演練的觀察窗口會不同長，且可能讓正在處理中的 response 卡在「完成一半」的
未定義狀態（MTTR 算完成還是 unknown？）。純時間制完全避開這個邊界案例。

## 5.2 Instructor 手動中止不在本節範圍內

設備故障、需要臨時喊停等操作層需求，屬於 WS7 Instructor Console 的職責，不是遊戲規則本身，
不影響 §5.1 定義的「正常結束條件」。

---

# 6. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼不在本檔決定 |
|---|---|
| Flag 提交格式、防暴力猜測 rate limit | 實作層細節，非架構決策，答案不影響下游 workstream 的 schema／依賴關係 |
| Blue 側個人化計分 | P2 現有 KPI 本來就是全場級指標，沒有「哪個分析師」概念，維持現狀不衝突 |
| Multi-team 對戰 | SA §16 V3 才規劃，MVP／V1 範圍外 |
| Purple KPI → Battleboard 的狀態化轉譯規則 | §1.2 已定案不做，若未來要做屬新決策，需另開討論 |

---

# 7. 決策總表

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 1 | Red／Blue 計分關係 | 各自獨立評分，非零和 | §1.1 |
| 2 | Purple 要不要有分數 | 不參與計分，只出 KPI | §1.2 |
| 3 | Objective 與 Action Registry 關係 | 完全無關，無外鍵 | §2.1 |
| 4 | Objective 完成判定 | 混合制（遙測＋提交） | §2.2 |
| 5 | Hint 扣不扣分 | 扣分 | §3 |
| 6 | Difficulty 效果由誰定義 | 純內容標籤，無平台公式 | §4 |
| 7 | 演練結束條件 | 純時間制，不提早結束 | §5.1 |
| 8 | 計分粒度 | Red 分個人；**Blue 有 `player_id` 與「我這段」狀態但不計個人分**（2026-08-12 #65 修訂）；Action Registry 隊級 | §1.3 |
