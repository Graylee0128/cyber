# Experience Layer — 最小 cue 契約與 SSE 投影架構

> 設計稿。定義 Campaign 的 Experience Layer 要長什麼樣、怎麼疊在既有 Core Event SSE bus 上。
> **不含實作**——這裡定契約與架構，落地是 Experience Layer 實作子票（見 [#153](https://github.com/Graylee0128/cyber/issues/153)）。

## 鐵則

> **Experience Layer 只能消費 / 投影 gameplay event，不得成為 detection / scoring 的 source of truth。**

驗收方式：把 BGM、動畫、SFX、甚至整個 Battleboard 關掉，核心 Scenario / Detection / Scoring 必須仍完整運作。任何「關掉特效就影響判分/偵測」的設計都是違規。

---

## 既有基礎（不重造）

平台已有一條 **Core Event SSE bus**，Experience Layer 疊在它上面、不另開資料平面：

| 元件 | 位置 | 作用 |
|---|---|---|
| 事件資料平面 | `src/range_core/event_stream.py` | `core_events.seq` bigserial cursor、`Last-Event-ID` resume、**per-subscriber clearance 投影**（`disclosure.project_fields`） |
| HTTP 端點 | `GET /api/events/live`（`src/range_core/api.py`） | SSE 串流，單串最長 300s |
| 前端 client | `Gateway.stream()`（`ui/assets/api.js`） | `EventSource` 對 `/gw/<identity>/core/api/events/live` |
| 消費者 | Battleboard / Instructor / Blue SOC 走 SSE；Player / Purple 走 poll | 目前是 **raw Core Event feed + clearance masking**，不是 experience/phase 投影 |
| 唯一時間概念 | `exercise.ends_at` → `countdown()`（`ui/assets/api.js`） | 單一回合倒數；**無** phase / chapter 概念 |

**淨新增**：phase / chapter / campaign 狀態機、experience-cue 投影、Instructor 的 phase/show control——這些是 Experience Layer 實作子票的範圍。

---

## 架構：gameplay event → Experience Projection → surfaces

```text
          Core Event SSE bus（既有，clearance-masked）
       Attack / Detection / Score / Phase Event
                        │
                        ▼
              Experience Projection            ← 淨新增，純消費者
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
   Battleboard      Player UI        Blue SOC
   visual/phase     feedback         alert cue
        └───────────────┼────────────────┘
                        ▼
                  BGM / SFX / UI FX

   Instructor Console ──► phase / reveal / show control
```

- **Experience Projection** 訂閱既有 SSE，把 gameplay event 映成 experience cue，再推給各 surface。它不寫回 CoreEventStore、不改分數、不產生偵測。
- **clearance 投影已在資料平面做掉**：Experience Projection 拿到的已是該身分可見的欄位，投影邏輯不需重做遮罩，也不得繞過它去讀更高 clearance 的欄位。

---

## 最小 experience-cue 契約

一則 experience cue 是 Core Event 的**投影產物**，欄位形狀（設計，實作子票落成型別）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `cue_id` | str | 穩定識別，供前端去重 / 動畫綁定 |
| `source_event_seq` | int | 來源 Core Event 的 `seq`（可溯源；投影不脫離事實） |
| `kind` | enum | `phase_transition` / `major_event` / `objective_complete` / `critical_alert` / `countdown` / `reveal` |
| `visibility` | enum | `public`（Battleboard/Audience）/ `role:<red\|blue\|purple\|instructor>` |
| `chapter` | str? | 所屬 Chapter（`CH1`…`FINAL`），phase/major event 才有 |
| `phase` | enum? | `briefing`/`initial`/`escalation`/`critical`/`final`/`debrief` |
| `presentation` | object | 純呈現提示：`{ label, severity, sfx?, bgm_phase?, animation? }`；**不含**任何 payload / technique / 答案 |
| `mutable_core` | 恆為 false | 契約層宣示：cue 不攜帶、不影響 scoring/detection 狀態 |

**可見性規則**：`visibility=public` 的 cue 一律不得帶 payload、technique、答案；這些只能出現在 `role:purple` / `role:instructor`。投影層負責這道過濾，等同 Battleboard/Audience 的資訊天花板。

### cue 種類 × 觸發來源（設計 baseline）

| kind | 典型來源 Core Event | 預設 visibility | 呈現 |
|---|---|---|---|
| `phase_transition` | Instructor 手動 advance（MVP）/ 未來 phase event | public | Battleboard phase 切換 + BGM phase |
| `major_event` | attack.detected / objective 里程碑 | public（去識別文案，如「Initial Access Detected」） | Battleboard 動畫 |
| `objective_complete` | scoring 事件 | role + public 版 | Player SFX + Battleboard 加分動畫 |
| `critical_alert` | high severity attack.detected | role:blue（+ public 去識別版） | Blue SOC pulse + 短 SFX |
| `countdown` | `exercise.ends_at` / final push | public | Battleboard 倒數 |
| `reveal` | Instructor reveal（淨新增端點） | 由 Instructor 指定 | 對應 surface 揭露下一段 briefing |

---

## Instructor as Game Master（MVP 邊界）

現況 Instructor 只有 start / stop / reset / objectives-sync / admission 生命週期，**沒有** reveal / inject / override（`ui/README.md` 已記為 out of scope 的三缺口）。Experience Layer 要讓 Instructor 變「導演」，MVP 只加：

- Start briefing / exercise
- **Reveal / advance Chapter**（新端點；phase_transition + reveal cue 的來源）
- Announcement（major_event cue）
- BGM phase 切換（先手動，避免 scenario event 誤判讓音樂亂跳）
- Pause / resume、Final countdown、End / result reveal

**不做**（out of scope）：Override Score、Inject arbitrary Event——維持 Experience 不成為 gameplay source of truth 的鐵則。

---

## MVP boundary（Experience 實作子票）

**Must**：Battleboard countdown / phase / major-event 呈現；Blue SOC critical-alert visual + 短 SFX；objective-complete SFX；Instructor 的 phase / reveal / audio 控制；`mute` / `master volume` / `reduced-motion` 必須存在。

**Nice / follow-up**：3–4 段 dynamic BGM 系統、大量 animation / meme 素材、燈光 / LED / 實體按鈕、場地音響 automation、外部 show-control。這些不得阻塞 Campaign 的 scenario/content 交付。

---

## 對 Scenario 設計的回饋

每條 chain 除技術 metadata，設計稿（見 `chapters/`）另回答：這段在故事裡發生什麼、哪些事件值得投影成 `public` major_event、哪些只給 Red/Blue/Purple、是否有 audiovisual cue、Instructor 何時 reveal / skip / advance。這些答案就是上面 cue 契約的填充來源。
