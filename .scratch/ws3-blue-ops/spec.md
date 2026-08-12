# Spec — WS3 Blue Operations

- **Status**: draft（定案後比照 P1 遷出至 `docs/`；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-11 grilling session（九題逐一確認，本檔為落地記錄）
- **上位文件**：[SA](../../資安攻防平台_系統架構設計文件_v0.1.md) §4.2、§5.2、§9、§12.3；[purple_platform_plan.md](../../purple_platform_plan.md) §3.4、§4.1
- **上游依賴**：[WS1 遊戲規則](../ws1-game-design/spec.md)、[WS5 Range Core](../ws5-range-core/spec.md)、[WS2 Scenario/Target](../ws2-scenario-target/spec.md)
- **既有實作**：response 鏈（queue → agent pull → ipset）、Evidence API 的 clearance 模型、`visibility` 事件級投影

WS3 是 SA §4.1 的內容線之一，負責 Incident、Investigation、Response Workflow。
落點是 **Z-APP 操作、Z-TARGET 落地**（SA §4.2）。

**本檔處理的核心問題不是「藍隊的動作怎麼分類」，而是「藍隊到底在做什麼」** ——
grilling 第一題就查出：偵測到攻擊之後，封鎖是機器自動做的，SA §9 給 Blue 的四個 objective
有三個是機器在做。分類法是那個問題的下游。

---

# 0. 本檔新增的兩條可複用判準

WS2 spec §0 已立兩條（擁有 vs 引用、預付判準）。本輪再加兩條。

## 0.1 答案空間決定層級

> 同一個問題，在不同的答案空間下，可以一個是實作細節、一個是架構決策。

「猜錯了怎麼辦」在紅隊是實作細節（[WS1 spec §6](../ws1-game-design/spec.md) 已如此判定 ——
flag 是高熵字串，猜不到）；在藍隊是架構決策（§4.2 —— technique 是 5 選 1，不定規則就是送分）。

**不是誰比較重要，是熵不同。** 判斷一件事該不該進 spec 時，先問它的答案空間有多大。

## 0.2 讀設定可以用檔案，下命令必須是呼叫

[WS2 spec §2.1](../ws2-scenario-target/spec.md) 刻意避開 Z-APP ↔ Z-MGMT 的執行期依賴，
`expected_sources` 走共享宣告檔而非 API。**本檔 §5.2 的封鎖路徑避不掉**，必須是跨區呼叫。

差別在於：**前者是「讀設定」，後者是「下命令」。** 設定可以在部署期固定下來；
命令帶有「此刻、對這個對象、做這件事」的時效性，檔案表達不了。

寫下這條是為了避免日後有人拿 WS2 §2.1 來反對 §5.2。

---

# 1. 藍隊到底在做什麼

## 1.1 人在迴圈：封鎖由藍隊觸發，不是自動

**現況是全自動的。** [receiver/__init__.py:73](../../src/purple/receiver/__init__.py)：

```python
if core["event_type"] == "attack.detected" and response_queue is not None:
    response_queue.enqueue(ResponseCommand(event_id=..., source_ip=...))
```

`attack.detected` → 自動入佇列 → agent pull → ipset。**全程零人工。**

**改為**：偵測產生的是**待處置建議**，藍隊在 SOC Console 按下才真的執行。

**為什麼**：

1. **不必拆掉任何已完成的東西。** queue → agent pull → ipset → `response.executed` 整條鏈原封不動，
   `#17` 驗的也還是同一條。**變的只有「誰 enqueue」** —— 從 receiver 那個 `if`，變成一次 API 呼叫。
2. **維持自動的話，演練最好看的一刻會消失。** 「藍隊看到告警 → 判斷 → 封鎖 → 攻擊真的斷了」
   是這個產品唯一能當場演給人看的閉環。改成藍隊只做分類，Battleboard 的藍隊欄永遠沒有動作。
3. **分級（低嚴重度自動、高嚴重度要人）不做** —— 那是在零實例時訂分級規則，且會產生兩種 MTTR 語意，
   撞上 [plan §3.2](../../purple_platform_plan.md)「所有 scenario 一致，可比」。

## 1.2 自動封鎖保留為測試載具的能力，不是演練預設

沒有藍隊的場次（純管線測試、demo、只練紅隊）仍需要鏈路能自己跑完。

**處理方式與 [WS2 spec §3.1](../ws2-scenario-target/spec.md) 的攻擊面二分同構**：

| 用途 | 觸發方式 |
|---|---|
| **演練**（可計分） | **人工**，藍隊按下 |
| **測試載具** | 自動，明確標記，不產生藍隊分數 |

兩者明確標記、互不冒充。**測試用自動、計分用人工。**

## 1.3 SA §9 四個 Blue objective 的重新定位

| Blue Objective | 原本誰做 | 本檔之後 |
|---|---|---|
| `Detect Attack` +100 | Grafana | 改為**藍隊接手／確認告警**，量注意到的速度 |
| `Identify Technique` +100 | rule 的 label 已寫好，且是 `public` | 改為**藍隊提交判讀**，答案遮蔽（§2） |
| `Contain < 60 sec` +150 | agent 自動封鎖 | **藍隊觸發**（§1.1） |
| `Resolve Incident` +100 | 人 | 不變（但「incident」的意義見 §3） |

SA §9 該節需標註：四個 objective 的**語意已由本檔重寫**，點數本身不變。

---

# 2. 判讀：把答案遮掉

## 2.1 `technique` 對紅藍雙方遮蔽

**現況**：[schema.py:40](../../src/purple/harness/schema.py) 的 `VISIBILITY_BY_EVENT_TYPE`
把 `attack.detected` 定為 `public`，而 Core Event 帶 `technique` 欄位 ——
**攻擊一被偵測，`T1190` 就已經寫在事件上發給所有人。**

於是 `Identify Technique +100` 是白送的。

**改為**：藍隊看到的告警**不含 `technique`**；藍隊自己從證據判讀並提交，
系統用 Grafana rule 的 `technique` label **自動比對評分**。

**為什麼不是砍掉這個 objective**：砍完之後藍隊只剩「按封鎖鈕」與「關單」，
而**判讀技法正是 SOC 分析師的核心工作**。這是資安訓練平台，不是按鈕反應速度測驗。

**為什麼不是維持現狀**：那違反 WS2 §3.1 立的紀律 ——
那裡拒絕了假漏洞（`capture_flag` 一個 curl 就拿到），理由是「分數會失去鑑別力」。
技法白送再給 100 分，是同一件事換一邊。

**這個機制有一個很好的性質：可自動評分。** 平台**已經知道正確答案**，
藍隊提交判斷，系統直接比對 —— 不需要人工評分、不需要教官在場。
與 [WS1 spec §2.2](../ws1-game-design/spec.md) 的 `capture_flag` 走提交比對是**同一個機制**，紅藍對稱。

## 2.2 紅隊也一起遮

`attack.detected` 是 `public`，紅隊也看得到。**遮 `technique` 要連紅隊一起遮**，
否則紅隊看得到、藍隊看不到，荒謬。

這**額外對上** SA §5.1「Red Team 不應直接看到 Detection Rule、Threshold、Falco Rule」——
紅隊只知道「我被抓到了」，不知道「被判成哪個技法」，更貼近真實對抗。

## 2.3 欄位級遮蔽是既有機制的延伸，不是新發明

現在的 `visibility` 是**每事件一個值**；本節需要的是**同一事件對不同身分露出不同欄位**。

這確實是延伸，但方向已經走過：[evidence/service.py:73](../../src/purple/evidence/service.py)
的 `resolver.resolve(event_id, caller=Caller(identity))` 已經在做 per-caller 投影，
證據每一行都帶自己的 `visibility`，clearance 由 identity 決定、**呼叫者無法自報**、
未知身分 fail loud。

**實作成本比聽起來低**：Blue SOC Console 還沒開始做，**沒有既有行為要改，只是第一次就規定對**。

---

# 3. Incident：不是實體

## 3.1 一切 event 級，`Incident Queue` ＝ event queue

**不建立 incident 實體。** 藍隊的動作對 `event_id` 操作，「關單」＝關掉一個 event。

**為什麼**：

1. **[WS5 spec §1.2](../ws5-range-core/spec.md) 已經替這題押過注**，理由是不可逆性 ——
   現在記 event 級，日後不論 incident 定義成什麼形狀都聚合得出來；
   現在記 incident 級，event 級資訊永久遺失。建立實體等於推翻一個已定案且理由更強的決策。
2. **預留分組欄位違反 [WS2 spec §0.2](../ws2-scenario-target/spec.md)**。那條說「在會被人手寫的地方預付，
   在有測試接住的地方不預付」。incident 分組是**執行期資料**，有測試接得住 —— 不該預付。
3. **規模上買不到東西。** 一場 30 分鐘的演練偵測事件數以個位數計。分組讓藍隊少按兩次按鈕，
   代價是多一個實體、多一套歸屬規則、多一層「這個 action 是對 incident 還是對 event」的歧義 ——
   而 `#36` 的反應時間量測正是靠 event 級歸屬才乾淨。

**P1 的 fingerprint 機制已經做掉一半的分組**：firing／resolved 共用同一個 `event_id`
（[receiver/__init__.py](../../src/purple/receiver/__init__.py)），同一次攻擊的起訖天然合成一筆。

## 3.2 已知代價

- SA §5.2 的 `Incident Queue` 要重新理解成 **event queue**，該處需標註
- 同一攻擊者同時觸發 SQLi 與暴力破解時，藍隊會看到**兩筆**要各自處置；真實 SOC 會併成一單

**破局觸發條件**：當一場演練的事件數多到藍隊處理不完，或出現「同一攻擊者的多筆事件必須
一起判斷才對」的 scenario 時，加 incident 實體。屆時是**由 event 級記錄聚合**，不是重做 ——
這正是 WS5 §1.2 預先保住的東西。

---

# 4. 動作與判讀規則

## 4.1 五個動作的封閉列舉

| 動作 | 對應 objective | 量什麼 |
|---|---|---|
| `acknowledge` | `Detect Attack` | 注意到的速度（見 §4.5 —— 起點是 Core Event，不是它） |
| `classify` | `Identify Technique` | 判讀正確與否（系統自動比對） |
| `contain` | `Contain < 60 sec` | 處置速度 |
| `resolve` | `Resolve Incident` | 收尾 |
| `dismiss` | —（誤報處置） | 誤報判斷是否正確 |

**封閉列舉，不是開放集合。** 開放集合＝分類法會漂＝不同場次的藍隊指標不可比，
再次撞上 plan §3.2 的可比性。

### 為什麼需要 `dismiss`

§2.1 要求藍隊提交技法判讀。若某條 Grafana 規則誤觸（紅隊做了無害的事卻踩到規則），
藍隊**沒有正確答案可提交** —— 只能亂猜然後被判錯。**那是用規則的缺陷扣人的分**，
正是 plan §3.4 反覆強調要避免的「冤枉藍隊」。

**裁決成本已經預先付掉**：「藍隊說這是誤報，對不對？」需要一個獨立於偵測規則的真相來源，
而 [WS2 spec §4.3](../ws2-scenario-target/spec.md) 已經要求建立那個東西 ——
靶機 app 對每個請求寫的執行證據 log，其存在與 Grafana 有沒有規則無關。
**沒有執行證據 → 藍隊判誤報正確。** 同一個機制服務兩個目的，不必新蓋。

### 刻意不放進清單的兩個

| 動作 | 為什麼不做 | 破局觸發條件 |
|---|---|---|
| `escalate` | 演練只有一個藍隊，沒有可升級的對象。真實 SOC 的分層在這個規模下是空的 | 出現多層藍隊 |
| `unblock` | Z-RED 只住紅隊，封錯的代價在 range 內趨近於零。真實 SOC 誤封會斷業務，這裡沒有業務可斷 | 靶機上出現「不該被封的合法流量」 |

屆時是往封閉列舉**加一個值**，不是改架構。

## 4.2 判讀一次定生死

`classify` 與 `dismiss` 每個 event **只有一次機會**，錯了該 objective 零分。

**為什麼不能無限重試**：候選只有 `config/techniques.yaml` 裡的少數幾個，
而 Console 勢必要給候選清單（總不能叫人憑空打字）。無限重試＝送分，
與 §2.1「把答案遮掉」的整個目的自相矛盾。

**為什麼不做遞減扣分**：遞減比例要訂多少？沒有任何資料可依據 ——
[WS1 spec §4](../ws1-game-design/spec.md) 拒絕過同樣的事（difficulty 公式），理由是「訂的是空氣」。
而且 hint 的扣分比例是**內容作者按 scenario 決定**的，藍隊判讀在 §4.3 定為**平台級固定**，
兩者機制不同源，硬套會長出誰都沒想清楚的第三套規則。

**對得上真實 SOC**：分析師把事件歸錯類就是歸錯了，下游的處置與統計都跟著錯。

**要配的一件事（UX 層，非架構）**：Console 對判讀提交要有明確確認步驟。
誤點一次歸零的惡感應該花在確認對話框上，不是花在「再給你一次機會」上 —— 後者會把機制掏空。

> 本節是 §0.1「答案空間決定層級」的來源：同一個問題在紅隊是細節、在藍隊是架構。

## 4.5 時間量測：兩段共用起點，不是接力

**起點永遠是 Core Event 的 `observed_at`**（[WS5 spec §1.1](../ws5-range-core/spec.md) 的定義），
不同動作是不同的**終點**：

```text
Core Event ──────► acknowledge     ＝ 注意到的速度   → Detect Attack
     │
     └───────────► contain         ＝ 處置速度       → Contain < 60 sec
```

**不是 `Core Event → acknowledge → contain` 三段接力。** 藍隊跳過 `acknowledge`
直接 `contain`，`Contain < 60 sec` 仍然成立。

> 本節是全域掃描（2026-08-12）補上的。切票時我一度把 `acknowledge` 寫成「反應時間的起點」，
> 那與 WS5 §1.1 直接衝突 —— `acknowledge` 是某一段的終點，不是任何東西的起點。
> `#49` 與 `#36` 已更正。

## 4.6 MTTR 的語意被 §1.1 改變了

ADR ⑦ 的三個終點定義**不變**，變的是 MTTR 中間包含什麼：

| | 人在迴圈之前 | 之後 |
|---|---|---|
| MTTR 涵蓋 | 偵測 → agent 輪詢 → ipset | 偵測 → **人注意到 → 人判斷 → 人按下** → agent 輪詢 → ipset |
| 它回答 | 「這套**系統**處置得多快」 | 「這個**團隊＋系統**合起來多快」 |

**「不得拿 MTTR 計分」的理由因此更強，不是變弱** —— WS5 §1.1 原本的理由是
「含藍隊控制不了的基礎設施延遲」；現在它**同時**含人與基礎設施，混在一起更不可拆。
藍隊計分仍走 WS5 自量的反應時間（§4.5）。

**兩種模式的 MTTR 不可混算**：測試載具模式（§1.2，無人工介入）回到純系統延遲，
與演練模式的數字放在同一張趨勢圖上比較是錯的。`#21` 與 `#28` 已補驗收條件。

## 4.3 Blue 計分參數是平台級，不進 scenario

四個 objective 的點數與 `Contain` 門檻住 `config/`，**所有 scenario 一致**。

**為什麼**：

1. **[WS2 spec §0.1](../ws2-scenario-target/spec.md)**：「紅方的東西 scenario 擁有；
   **藍方與平台的東西 scenario 只引用並驗證**」。Blue 計分參數是藍方的東西。
2. **[WS1 spec §4](../ws1-game-design/spec.md) 已經指定過藍隊難度的旋鈕，而且不是分數**：

   > 若某個 hard scenario 的作者想讓藍隊在能見度不足下作戰，**直接在該 scenario 的
   > Source Registry 定義裡少放幾個 `expected` 來源即可**

   機制已經有了。再給一個「改分數門檻」的旋鈕等於同一件事兩個開關，
   而第二個開關會讓**跨 scenario 的藍隊分數不可比**。
3. **「平台預設 ＋ scenario 可覆寫」不做** —— 有預設就會有人改，A 的問題以較慢的速度重演。

**直接後果**：`#36` 的「門檻由 scenario 宣告」要改成「取自平台設定」；
[WS2 spec §8](../ws2-scenario-target/spec.md) 範圍界線裡留的那一格就此關閉 —— **答案是不放進 scenario 檔**。

**代價**：作者無法針對特定情境放寬藍隊評分標準。這是**特性不是限制** ——
`Contain < 60 sec` 量的是人的反應速度，而人的反應速度不該因為題目換了就換一把尺。

## 4.4 藍隊沒有 hint

**Hint 是紅隊機制。**

**為什麼**（不只是「無處可掛」，而是內容上自我毀滅）：

| 藍隊要 hint 的動作 | hint 會寫什麼 | 後果 |
|---|---|---|
| `classify` | 「這可能是 T1190」 | **直接洩漏自動評分的答案**，把 §2.1 與 §4.2 一起作廢 |
| `contain` | 「按封鎖鈕」 | 沒有資訊量 |
| `acknowledge` / `resolve` | 「點一下」 | 同上 |

**紅藍不對稱有理由，不是遺漏**：紅隊 hint 給的是**攻擊路徑的提示** ——
路徑空間大，提示有梯度（「看看回應 body」不等於直接給 flag）。
藍隊唯一有智力含量的動作是少數選一的判讀，**任何有用的提示都直接跨過那條線**。

SA §5.1 把 Player Portal 的 audience 寫成 Red + Blue 並列 `Hint`，
那是還沒區分兩邊工作性質時的草稿 —— 該處需標註 **`Hint` 只適用 Red**。

**破局觸發條件**：若日後藍隊多出**有梯度**的工作（自己寫偵測規則、自己查 raw log 找 IOC），
hint 就有意義了。屆時它住平台級，因為 Blue objective 在那裡。

---

# 5. 身分與路徑

## 5.1 藍隊不個人化，clearance 掛在 Console 服務上

**現況**：Evidence API 的 clearance 模型是完整實作的
（[resolver.py:43](../../src/purple/evidence/resolver.py)）——

```python
VISIBILITY_RANK  = {"public": 0, "blue": 1, "purple": 2, "instructor": 3}
CALLER_CLEARANCE = {"red": 0,    "blue": 1, "purple": 2, "instructor": 3}
```

且「clearance 由 `identity` 決定，**呼叫者無法自報**」、未知身分 fail loud。

**空白**：藍隊的 `identity` 從哪來，沒有任何機制。`#32` 的名冊是 player ↔ source IP，
那六個位址是 Z-RED 的 kali `.11`–`.16`，**紅隊專屬**；`#33` 明講「不另做帳號登入系統」；
四區裡**沒有藍隊的位置**。clearance 表裡的 `"blue": 1` 目前是**沒有人能取得的身分**。

**決定**：**Blue SOC Console 這個服務本身持有 blue clearance**，誰坐在螢幕前不區分。

**為什麼**：[WS1 spec §1.3](../ws1-game-design/spec.md) 已經定案 **Blue 側不做個人化** ——
「P2 現有的 KPI 本來就是全場級指標，沒有『哪個分析師』的概念」。
既然藍隊分數本來就不分人，**替藍隊建立個人身分機制是為一個不存在的需求付錢**。

給藍隊網段要動 `zones.env`、防火牆、時鐘節點；做帳號系統則是 `#33` 剛拒絕過的東西。

**這正好對上 SA §12.3 的措辭**：「Blue **透過 Blue SOC Console 操作**，不取得 Z-MGMT
底層存取權」—— 那句話講的就是**服務**是邊界，不是人。紅隊的瀏覽器打的是另一個服務
（Player Portal，clearance `red`=0），兩者天然分離。

**對 `#36` 的影響**：WS5 §1.2 的 Blue Action 契約寫「誰、何時、對哪個 `event_id`」，
**「誰」在本決策下恆為 `blue`（隊，不是人）**。這不是遺漏，是與 WS1 §1.3 一致。

**升級觸發條件**：真要做藍隊個人化評分時，加 per-user 認證是**增量** ——
clearance 模型一個字都不用改，只是 identity 的來源從服務憑證換成使用者憑證。

## 5.2 封鎖路徑：Console → Range Core → response queue

藍隊按下按鈕那一刻要寫兩個地方：

| 寫哪 | 在哪一區 | 為了什麼 |
|---|---|---|
| Blue Action 記錄 | **Z-APP**（Range Core） | `#36` 的反應時間計分 |
| response 命令佇列 | **Z-MGMT**（[queue.py](../../src/purple/response/queue.py)） | agent pull → ipset 真的封鎖 |

**Range Core 是編排者**：先記 Blue Action，再呼叫 Z-MGMT 的佇列。

**為什麼**：[WS5 spec §1.1](../ws5-range-core/spec.md) 的定義本來就假設了這條路 ——

> 反應時間量的是「從 Core Event 到 **Blue Action 進入 Event Service** 的時間差」

「進入 Event Service」只有在 Blue Action **先**抵達 Range Core 時才成立。

**另外兩條各有致命傷**：

| 路徑 | 致命傷 |
|---|---|
| Console 雙寫 | 兩個 zone 沒有交易保證。記錄成功但佇列失敗＝得分卻沒封鎖；反之＝封鎖了卻沒分 |
| 從 `response.*` 事件反推 | 量到的時間**含 agent 輪詢與 ipset 套用** —— 那是 MTTR，而 WS5 §1.1 明文禁止拿它計分（「等於用系統效能評人」） |

**代價，兩筆，都要認**：

1. **Range Core 成為封鎖路徑的單點。** 它掛了藍隊按不動封鎖。單機 range 上可接受，
   但要寫進 spec 而不是等它發生。
2. **新增一條 Z-APP → Z-MGMT 的執行期依賴。** 由 §0.2 那條判準正當化：
   讀設定可以用檔案，下命令必須是呼叫。

**`#20` 的優先序因此上升** —— 從「Purple Console 要用 `APP → MGMT`」變成
「**封鎖路徑要用**」。那四條規則尚未實作。

---

# 6. Evidence 存取

Blue SOC Console **直連** Z-MGMT 的 Evidence API（帶服務身分），
與 Purple Console → Evaluation API 同形狀，都由 `#20` 的 `APP ↔ MGMT` 規則涵蓋。

**這不違反「Blue 不取得 Z-MGMT 底層存取權」**（SA §12.3）：
Evidence API 是**受管端點** —— 固定 5 分鐘上下文窗、clearance 過濾、
`GET /evidence/{event_id}` 一個 event 一次 —— **不是 raw LogQL 查詢權**。

這條界線與 [plan §4.1](../../purple_platform_plan.md) 給 Purple Console 劃的是同一條：
**受管端點可以給，raw query 不給。**

Blue clearance = 1，看得到 `public` + `blue` 兩級的證據行；`purple` 與 `instructor` 級看不到。

---

# 7. 對既有票與程式碼的影響

| 落在誰 | 什麼事 | 依據 |
|---|---|---|
| **歷史 PR #39／原 `#17`** | T4 若靠 receiver 自動 enqueue 驅動整條鏈，人在迴圈後要改由測試載具觸發；未完成驗證由 canonical `#44` 承接 | §1.1、§1.2 |
| `src/purple/receiver/__init__.py` | 自動 enqueue 降級為測試載具能力。改動小、語意大 | §1.1 |
| `#36` | 三處：門檻改平台級（§4.3）／「誰」恆為 `blue`（§5.1）／契約要含五個動作（§4.1） | — |
| `#36` | SSE 的 visibility 過濾要加**欄位級**遮蔽（`technique`），現在只有事件級 | §2.3 |
| `#20` | 優先序上升 —— 從「Console 要用」變成「封鎖路徑要用」 | §5.2 |
| `#21` | MTTR 標明涵蓋人工決策時間；兩種模式分開統計 | §4.6 |
| `#28` | 報告的 MTTR 要標明同上；無藍隊介入時顯示 `unknown` 而非 0 | §4.6 |
| `#49`／`#36` | 更正：`acknowledge` 不是反應時間的起點 | §4.5 |
| [WS2 spec §8](../ws2-scenario-target/spec.md) | 範圍界線那一格關閉：Blue 門檻**不進** scenario 檔 | §4.3 |
| SA §5.1 | `Hint` 只適用 Red | §4.4 |
| SA §5.2 | `Incident Queue` ＝ event queue | §3.1 |
| SA §9 | 四個 Blue objective 語意重寫，點數不變 | §1.3 |

---

# 8. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼不在本檔決定 |
|---|---|
| 演練結束時未完成的判讀 | 由 [WS1 spec §5.1](../ws1-game-design/spec.md)（純時間制）直接推導：時間到凍結，未完成不計分 |
| Console 的確認對話框與互動細節 | UX 實作層。§4.2 只要求「要有確認」，不規定長相 |
| Battleboard 上藍隊呈現什麼 | WS7 ＋ [plan §3.9](../../purple_platform_plan.md)（公開層用狀態不用比率）已有規範 |
| AI 輔助藍隊（SA §13.1 的 Alert Summary 等） | V2 |
| 藍隊自訂偵測規則 | 不在 v1。若做，`#4.4` 的 hint 破局條件會同時觸發 |

---

# 9. 決策總表

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 1 | 封鎖由誰觸發 | 人在迴圈；自動保留為測試載具能力 | §1.1、§1.2 |
| 2 | 佈景 objective 怎麼辦 | 遮掉 `technique`，藍隊提交判讀，自動評分 | §2 |
| 3 | Incident 是實體嗎 | 否，一切 event 級 | §3.1 |
| 4 | 動作清單 | 封閉列舉五個（含 `dismiss`） | §4.1 |
| 5 | 藍隊身分 | 不個人化，clearance 掛在 Console 服務 | §5.1 |
| 6 | Blue 計分參數 | 平台級固定，不進 scenario | §4.3 |
| 7 | 判讀錯了 | 一次定生死 | §4.2 |
| 8 | 藍隊 hint | 沒有 | §4.4 |
| 9 | 封鎖路徑 | Console → Range Core → response queue | §5.2 |
| — | 新判準（浮現，非提問） | 答案空間決定層級；讀設定用檔案、下命令用呼叫 | §0 |
