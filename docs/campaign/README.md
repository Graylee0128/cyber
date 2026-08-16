# Campaign Pack v1 — Campus Edition

> 活契約。本目錄是 [#153](https://github.com/Graylee0128/cyber/issues/153) Phase 1 Content Design 的交付物。
> **設計稿，不含任何可執行程式或可載入的 `metadata.yaml`**——漏洞 app code、Falco/Grafana rule、scenario package 都是各實作子票的產物（見 [#153](https://github.com/Graylee0128/cyber/issues/153) 子票拆分）。

## 這份文件是什麼

Cyber 要從「資安攻防練習平台」往 **event-driven / immersive cyber exercise experience** 走。第一版把三層抽象定義清楚：

| 層 | 是什麼 | 這版怎麼實現 |
|---|---|---|
| **Scenario** | 技術內容（真實攻擊面、遙測、判分、reset） | 沿用現有 WS2 Scenario contract，不新增第二套 schema |
| **Campaign / Story** | 為什麼這些事件會發生、五題怎麼串成一個故事 | 本 Campus 世界觀 + 五個 Chapter |
| **Theme / Experience** | 玩家怎麼透過 UI、文案、聲音、節奏感受到故事 | `experience-contract.md` 的最小 cue 契約 + SSE 投影 |

**設計原則（Architecture rule）**：Experience Layer 只能**消費 / 投影** gameplay event，不得成為 detection / scoring 的 source of truth。關掉 BGM、動畫、Battleboard 後，核心 Scenario / Detection / Scoring 必須仍正常運作。

---

## Campus World Setting

### 受害組織

**明湖科技大學（Minghu Institute of Technology, MIT-Minghu）** 的「校園生活服務平台」。這是一套把太多東西塞在一起的老系統：

- 福利社線上商城（買飲料、社團器材、活動票券）
- 作業 / 社團海報上傳
- 系統報修 / 診斷小工具
- 學生資料 API（選課、成績、個資）

四個功能長在同一台主機（`range-target`，Z-TARGET / VLAN 20）上——這既是敘事設定（「一間學校的一套系統」），也對齊平台的單主機約束（multi-host 是 out-of-scope）。

### 故事主線

一名攻擊者盯上明湖科大，從最不起眼的福利社商城打起，一步步擴大 foothold，最後撈走全校學生個資。五個 Chapter 是這**同一場入侵事件**的五個階段，不是五張互不相關的題目。

```
CH1 The Breach      福利社商城被撬開，攻擊者摸到後台
   │
CH2 Foothold        傳一張「海報」上去，其實是後門
   │
CH3 The Stolen Key  借系統的手去偷雲端的鑰匙
   │
CH4 Ghost in the System   在報修工具裡住下來，關機也趕不走
   │
FINAL The Leak      全校學生的成績與個資，被一頁一頁搬走
```

### 角色視角（同一 Chapter，不同揭露）

| 角色 | 看到什麼 |
|---|---|
| **Red** | Mission Brief：攻擊目標與可用情報，**不含**攻擊路徑（比照現有 `briefing.md` 慣例） |
| **Blue** | SOC Incident Brief：異常跡象與防禦任務，不含後續答案或完整 kill chain |
| **Purple** | 完整 attack-chain、telemetry、coverage、detection gap |
| **Instructor** | 完整 Campaign、Chapter progression、reveal / skip / advance 控制 |
| **Audience / Battleboard** | 公開版事件進度（「Initial Access Detected」等），**不揭露** payload / technique / 答案 |

**故事層可以線性，技術底層不得 hard-gating。** 每條 Scenario 應可獨立啟用、reset、skip；Instructor 依活動時間調整 Chapter 順序或只選部分內容。串接由故事負責，Scenario Engine 不因此新增 hidden dependency。

---

## Scenario Matrix

刻意排除 volumetric DDoS / 純負載攻擊。五條在 attack surface、父技術、Blue telemetry 焦點上有實質差異。

| Chapter | 校園外皮 | Surface（模組） | Attack chain | 父技術 | Blue 偵測焦點 | Detection 狀態 |
|---|---|---|---|---|---|---|
| **CH1 The Breach**（現有） | 福利社商城 | `product-sqli` / `mysql-3306` | SQLi → credential reuse / DB pivot → vault flag | T1190 → T1078 | DB / credential / pivot | 有覆蓋（`SQLInjectionBurstTarget`）+ 刻意 gap（T1078 無規則） |
| **CH2 Foothold**（已實作） | 海報上傳 | `poster-upload` / `poster-render` | Upload bypass → Web Shell → Linux Privesc | T1190 → T1505 → T1548 | Falco：webshell exec + sudo find 濫用 | 全覆蓋（`WebShellUploadTarget`＋`LocalPrivescTarget`），無 intentional gap |
| **CH3 The Stolen Key**（已實作） | 網址預覽 | `link-preview` / `internal-api` | SSRF → Metadata Credential Theft → API Pivot | T1190 → T1552 → T1550 | app-log：`ssrf_suspected` | 部分覆蓋（`EgressAnomalyTarget`＝T1552；T1550 為 intentional gap） |
| **CH4 Ghost in the System** | 系統報修/診斷 | `system-command` | Command Injection → Reverse Shell → Cron Persistence | T1190 → T1059 → T1053 | process / command-line telemetry | 待實作票建 rule |
| **FINAL The Leak** | 成績/個資 API | `authenticated-api` | IDOR → Sensitive Data Access → Exfiltration | **T1087**★ → **T1213**★ → **T1567**★ | 行為 / 存取模式異常 | **刻意 detection gap 教學** |

★ = `config/techniques.yaml` 目前沒有、待 FINAL 實作票新增的父技術（`T1550` 已隨
CH3 落地補上）：

| 新增父技術 | 名稱 | tactic | 用於 | 狀態 |
|---|---|---|---|---|
| `T1550` | Use Alternate Authentication Material | lateral-movement | CH3 API pivot | ✅ 已新增（CH3） |
| `T1087` | Account Discovery | discovery | FINAL IDOR 列舉 | 待新增 |
| `T1213` | Data from Information Repositories | collection | FINAL 敏感資料存取 | 待新增 |
| `T1567` | Exfiltration Over Web Service | exfiltration | FINAL 外洩 | 待新增 |

> **層級規則**：`techniques.yaml` 明令父子技術不可並存（否則 coverage 重複計數，載入時擋下）。票裡原本寫的 `.003 / .001 / .005 / .006` 子技術**一律折成父技術**（T1505 / T1548 / T1552 / T1213）。

**FINAL 特別保留**：IDOR → exfil 缺少典型 malicious payload signature，是「看得到操作、卻沒有規則能一眼認出這是攻擊」的行為型案例——刻意當作 **detection gap** 教學與 Purple 討論素材（比照 ADR ③ 的 DETECTION_GAP vs VISIBILITY_GAP）。

各章逐條的 Phase-1 七問與偵測預先指定見 [`chapters/`](./chapters/)。

---

## 60 分鐘 Pacing（baseline）

Campaign 不是把五題排隊，而是有活動節奏。第一版 dry-run baseline：

| Phase | 約略時間 | Experience | 對應 Chapter |
|---|---:|---|---|
| Briefing | 0–5m | 世界觀 / 角色 / mission intro | — |
| Initial Incident | 5–15m | 第一個 foothold / 低強度 tension | CH1 |
| Escalation | 15–35m | attack chain 擴大、Blue telemetry 增加 | CH2, CH3 |
| Critical Phase | 35–50m | persistence / sensitive access / major incident | CH4 |
| Final Push | 50–60m | final objective + countdown | FINAL |
| Debrief | 結束後 | score reveal + incident timeline / coverage review | — |

Chapter 不必與時間區間一對一。Instructor 保有調節能力。量尺與記錄項見 [`dry-run-template.md`](./dry-run-template.md)。

---

## Tone / Theme —— Campus Edition

TA 以校園 / 營隊 / 約 20–30 歲參與者為主。基準：

> **80% immersive cyber world / 20% humor & meme**

- 世界觀認真，呈現可以不正經——但 meme / copy 只是 **presentation**，不得降低 SOC 資訊判讀性，也不得洩漏 Scenario answer / payload / technique。
- 適合出現梗的位置：objective complete、critical alert、hint / retry、chapter transition、final countdown、result reveal。
- 校園化例句（示意，content-design 可再調）：
  - `ROOT ACCESS ACQUIRED — 恭喜，你拿到這台伺服器的鑰匙了。學校的，不是你的。`
  - `有人在正式環境裡開趴。💀`
  - `福利社的資料庫正在燃燒。This is fine. 🔥`

### 長期抽象：Theme / Experience Pack

同一套 Cyber Core 與 Scenario 可以換不同包裝，不複製 Scenario Engine：

```
Cyber Core → Scenario Pack → Campaign → Theme / Experience Pack
```

Campus 是 v1。未來可延伸 Standard / Corporate、After Dark 等 edition，或 Season 01 / 02 的不同世界觀。Theme Pack 可含：world setting、chapter naming、role briefing、visual theme、copy / meme / easter eggs、BGM / SFX、Battleboard presentation。

---

## ⚠️ 平台限制：v1 只有一個全域 flag（CH2 實作時發現，影響所有 submission objective）

`src/range_core/flags.py` 的 `SharedFileFlagSource`：v1 只有**一個環境級 flag**
（`current-flag.txt`），由 `range-up` 整場輪換一次，不是逐 scenario。Campaign 的整個
賣點是多個 scenario 同場開——若兩條 scenario 都設 `submission` objective，玩家撈到
任一條的 flag 就能直接拿去交另一條，完全不用碰它的漏洞。這是真的評分漏洞，不是
可接受的權宜。

**CH2 因此不設 submission objective**，只用 telemetry objective（見
`chapters/CH2-foothold.md`）。**CH3/CH4/FINAL 實作前務必先確認 #45（逐 scenario 獨立
flag）是否已落地**：沒有就比照 CH2 全走 telemetry；有了才能安全地加 submission。

## 交付邊界（本設計 epic）

**做**：`docs/campaign/` 四類設計稿（本 README + experience-contract + chapters + dry-run-template）。
**不做**：任何 app code、Falco/Grafana rule、`metadata.yaml`、UI 程式——全部是 [#153](https://github.com/Graylee0128/cyber/issues/153) 子票的產物。

技術護欄：`ScenarioCatalog.from_directory`（`src/range_core/scenarios.py`）會掃 `scenarios/<id>/metadata.yaml` 跑跨檔參照驗證；本 epic 因此**不放**任何可載入的 scenario package，維持 CI 綠。
