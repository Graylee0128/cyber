# FINAL — The Leak

> **淨新增 chain**（設計稿；實作是 FINAL 子票）。
> 校園外皮：成績 / 個資 API 缺物件層授權，全校學生資料被一頁一頁搬走。
> **本章是刻意的 detection-gap 教學案例。**

## 故事位置（Phase-1 Q5）

攻擊者已在系統裡站穩。最後一步，他盯上學生資料 API——那個 API 只檢查「你有沒有登入」，卻不檢查「這筆資料是不是你的」。於是他把物件 ID 一個一個換過去（`?student_id=1001, 1002, 1003…`），把全校成績與個資撈出來、打包外送。沒有炸裂的 payload、沒有可疑的 shell，只有一連串「看起來很正常」的已認證請求。**Final Push：objective / exfiltration，也是整場最重的一擊。**

## Q1 玩家要完成什麼

利用已認證 API 的物件層授權缺失（IDOR），列舉並存取不屬於自己的學生資料，最後將其外洩（objective 標記證明成功取得跨物件的敏感資料並送出）。

## Q2 真正 attack surface 在哪

| Surface（模組） | 設計 |
|---|---|
| `authenticated-api` | 已認證的學生資料端點，缺少 object-level authorization：換 ID 即可讀他人資料（真 IDOR，非 mock） |
| exfil | 把跨物件撈到的資料送往一個外部 / web 服務端點，對齊 T1567 |

單主機：長在 `range-target` 的 `authenticated-api` 模組；exfil 目標以單機上的接收端模擬。

## Q3 平台如何知道完成

- **偵測類 objective**（telemetry）：`telemetry_signal.action_id` 指向 `idor-enumerate` —— 但**注意**：本章的偵測靠的是**行為 / 存取模式**（短時間內大量跨 ID 存取），而非 payload signature。判定 objective 完成可用「跨越 N 個不屬於呼叫者的物件」這種行為門檻。
- **奪旗 objective**（submission，至多一個）：外洩資料集裡 range-up 注入的哨兵標記，提交比對。

## Q4 Blue / Purple 應看到什麼 —— **這是重點**

- Blue：**沒有明顯的攻擊 payload**。看到的只是一堆 200 OK 的已認證請求。唯一線索是**行為 / 存取模式異常**（同一 session 短時間掃過大量不連續學生 ID）。
- Purple：這正是要討論的——**看得到 vs 看不到、看得到 vs 認得出** 的分野：
  - 遙測**在**（app log 有每一筆 API 請求）→ 不是 VISIBILITY_GAP。
  - 但**沒有規則**能把「一串正常請求」認定成攻擊 → 是 **DETECTION_GAP**。
  - 這與 CH1 的 T1078 gap 同類（都是 detection gap），但更深刻：CH1 是「有一步沒建規則」，FINAL 是「這類攻擊本質上難以用 signature 偵測，只能靠 behavioral / access-pattern」。

## Q6 資訊揭露

- Red briefing：目標＝那個資料 API 可疑、把不該拿的資料拿出來；不透露 IDOR。
- Blue briefing：SOC **不會**收到明確告警——這是刻意的。Blue 的挑戰是從「一切看似正常」中察覺異常存取模式（教學重點）。
- Public：「Mass Data Access in Progress」→ 最後的「Data Exfiltration」高潮事件（去識別）。
- Purple / Instructor：完整 IDOR → exfil 鏈，以及**為什麼這條沒有 signature 偵測**的 coverage 說明。

## Q7 值得投影的 major event —— Campaign 高潮

- `phase_transition`（public）：進入 **Final Push**（最後 10 分鐘，Battleboard 倒數 + final BGM）。
- `major_event`（public）：Mass Data Access → **Data Exfiltration**（全場最強的公開事件動畫）。
- `countdown`（public）：final countdown。
- `objective_complete`：`THE LEAK IS OUT` / `福利社的資料庫正在燃燒。This is fine. 🔥` —— result reveal 前的最後一梗。
- `critical_alert`：**刻意克制**——因為沒有 signature 告警，Blue SOC 不該在這章被 alert 洗版；這本身就是體驗的一部分（「太安靜」才是恐怖）。

---

## 偵測預先指定（實作票須建）

| 項目 | 值（設計指定） |
|---|---|
| 父技術 | **T1087**（Account Discovery，須新增，tactic `discovery`）→ **T1213**（Data from Information Repositories，須新增，tactic `collection`）→ **T1567**（Exfiltration Over Web Service，須新增，tactic `exfiltration`）——**三個父技術都要新增至 `techniques.yaml`** |
| Grafana alert title | **無**（這是刻意的）。可選擇建一條 behavioral 規則 `AnomalousAccessPatternTarget`（跨 ID 存取速率）當作「進階 / 選配」偵測，但**預設不建**，讓本章維持 detection gap 語意 |
| Falco rule | **無**（IDOR 是應用層授權問題，syscall 層看不出來——這點本身值得對 Blue 說明） |
| `expected_sources` | `[alloy, response-agent]`（app log 有每筆請求，遙測是在的——強調 visibility 有、detection 沒有） |
| `detection` | **`[]`（空）** —— 明示無 signature 覆蓋 |
| `intentional_gaps` | `[T1087, T1213, T1567]`（或至少 T1213/T1567）—— **DETECTION_GAP** 的主教材：遙測看得到、規則認不出 |
| `reset_scope` | `exercise`（IDOR 讀取多為唯讀；若 exfil 寫外部接收端狀態則 `environment`，實作票定案） |

> **護欄**：比照現有 `PurpleScope Uncovered Action` 的設計哲學——若哪天有人「好心」替本章加了 signature 規則，對應的決定性測試應該**變紅**，因為本章的教學價值就在於它**不被 signature 覆蓋**、只能靠 behavioral / access-pattern。這條護欄由 FINAL 實作票建立。

## 為什麼 FINAL 與其他四章本質不同

| | CH1–CH4 | FINAL |
|---|---|---|
| 攻擊特徵 | 有 payload / 異常 exec / 異常 egress | **無 signature，全是「正常」請求** |
| Blue 偵測 | signature / rule firing | **只能靠 behavioral / access-pattern** |
| gap 類型 | 有覆蓋（或單點 detection gap） | **整條刻意 detection gap** |
| 體驗 | alert 有聲 | **刻意安靜——「太乾淨」才是線索** |

這讓 FINAL 不只是「第五題」，而是整個 Campaign 對 Purple / Blue 最有教學價值的一章：把「visibility gap ≠ detection gap」「signature 偵測的極限」講清楚。

## 實作票 open questions（Phase 2）

- IDOR 端點的認證模型（要先有 login → 這牽涉平台目前 target 無 auth，實作票須加最小認證面）。
- behavioral 判定 objective 的門檻（跨幾個非自有物件、多長時間窗）。
- exfil 目標的模擬方式與 `reset_scope`。
- 三個新父技術（T1087/T1213/T1567）的 tactic 歸屬與 `techniques.yaml` note。
