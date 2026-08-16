# CH3 — The Stolen Key

> **淨新增 chain**（設計稿；實作是 CH3 子票）。
> 校園外皮：平台的「網址預覽 / 縮圖」功能被當成跳板，去偷雲端環境的鑰匙。

## 故事位置（Phase-1 Q5）

平台有個貼心功能：貼一個網址，它幫你抓縮圖 / 預覽。攻擊者發現這個功能會**替你去請求任意內部位址**——於是借它的手打進雲端 metadata 端點，偷出一組雲端憑證，再拿這把「別人的鑰匙」橫向存取原本碰不到的內部 API。**Escalation 的第二步：從立足點延伸到雲端身分。**

## Q1 玩家要完成什麼

透過 SSRF 讓伺服器去打內部 metadata 端點、取回雲端憑證，再用該憑證存取一個原本需要授權的內部 API（拿到那裡的 objective 標記）。

## Q2 真正 attack surface 在哪

| Surface（模組） | 設計 |
|---|---|
| `outbound-request` | 網址預覽 / 縮圖功能：接受使用者提供的 URL 並由伺服器發出請求，缺少對內部網段 / metadata 位址的過濾（真 SSRF） |
| metadata 端點 | 單機上模擬的「雲端 metadata」服務（固定位址，回一組**當場注入**的假憑證），對齊 T1552 |
| 內部 API pivot | 需該憑證才可存取的內部端點，對齊 T1550 |

單主機：SSRF 目標（metadata 服務 + 內部 API）都在 `range-target` 上以本機服務模擬，不新增主機、不打真雲。

## Q3 平台如何知道完成

- **偵測類 objective**（telemetry）：`telemetry_signal.action_id` 指向 `ssrf-metadata-theft`（伺服器對 metadata 位址發出的請求）。
- **奪旗 objective**（submission，至多一個）：用偷到的憑證存取內部 API 取得的標記，提交比對。憑證與標記皆 range-up 當場注入，未經 SSRF + pivot 取不到。

## Q4 Blue / Purple 應看到什麼

- Blue：**egress 異常**（伺服器對內部 / metadata 位址發出非預期 outbound 請求）+ **metadata endpoint 存取**。
- Purple：coverage 顯示 T1552（憑證讀取）有覆蓋，但——設計選擇——**T1550（用偷來的憑證做 API pivot）刻意留一個較弱覆蓋或 note**，讓 Purple 討論「讀到憑證」與「證明憑證被使用」之間的證據落差（呼應 `techniques.yaml` 對 T1552 的 note：讀取 ≠ 使用）。

## Q6 資訊揭露

- Red briefing：目標＝那個預覽功能可疑、拿到內部 API 的東西；不透露 SSRF / metadata 手法。
- Blue briefing：SOC 收到「伺服器發出可疑 outbound、打到內部 metadata」的 egress 告警。
- Public：「Internal Pivot Detected」去識別事件。
- Purple / Instructor：完整 SSRF → metadata → pivot 鏈與 coverage / 證據落差。

## Q7 值得投影的 major event

- `major_event`（public）：Internal Pivot Detected。
- `critical_alert`（role:blue）：異常 egress → metadata（高嚴重度）。
- `objective_complete`：`STOLEN KEY IN HAND` —— 可掛校園化 meme copy。

---

## 偵測預先指定（實作票須建）

| 項目 | 值（設計指定） |
|---|---|
| 父技術 | T1190 → **T1552**（Unsecured Credentials，已存在）→ **T1550**（Use Alternate Authentication Material，**須新增至 `techniques.yaml`**，tactic `lateral-movement`） |
| Grafana alert title（新建） | `EgressAnomalyTarget`（伺服器對內部/metadata 位址的 outbound）、`MetadataAccessTarget`（metadata 端點被存取） |
| Falco rule（新建，可選） | `PurpleScope Outbound Metadata Request`（若走 syscall 層 connect 偵測；或純以 app egress log + Grafana 判定，實作票二選一） |
| `expected_sources` | `[alloy, response-agent]`（走 app egress log；若採 Falco connect 偵測則加 `falco`） |
| `detection` | `[EgressAnomalyTarget, MetadataAccessTarget]` |
| `intentional_gaps` | 候選：`[T1550]` —— 讀到憑證有覆蓋，但「憑證被拿去 pivot」的使用證據刻意較弱，作為 Purple 證據落差討論點（實作票定案是否列為正式 gap） |
| `reset_scope` | `exercise`（若 SSRF + 讀取皆唯讀、注入的假憑證由 range-up 管理）／`environment`（若 pivot 會寫內部 API 狀態）——實作票依實際落地選定 |

> 與前兩章差異：CH1 打 DB、CH2 打檔案系統，CH3 打的是**網路 egress + 雲端身分**，telemetry 焦點在 outbound / metadata，與前兩章不重疊。新增 T1550 是本章的技術前置。

## 實作票 open questions（Phase 2）

- metadata 服務在單機上怎麼模擬最真實（固定 link-local 位址 vs 本機服務），且不誤導成真雲。
- SSRF 過濾繞過的具體漏洞形態（DNS rebinding 過度、blocklist 不全、redirect 跟隨）。
- T1550 pivot 是否寫狀態 → 決定 `reset_scope`。
