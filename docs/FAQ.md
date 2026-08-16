# Cyber FAQ

> 跨角色、跨情境的問答入口。回答「大家最常問什麼、現在正確答案是什麼」——
> 正式 contract 仍以 [Participant Guide](./participant-guide/README.md)、
> [Operator Guide](./operator-guide/README.md)、[Technical Handbook](./technical-handbook/README.md)、
> spec／ADR 為準。這裡的答案跟那些文件對不上時，以那些文件為準，請回報 drift。

每題標一個狀態：

- **Current** —— repo 現在已實作／已拍板
- **Planned** —— 已有 canonical issue／spec，但尚未完整落地
- **Future / Idea** —— 產品方向，尚未承諾施工

---

## A. 角色與 UI

### Q1. Cyber 一場活動有哪些人？

**Current.** 五類角色：🔴 Red Player、🔵 Blue Player、🟣 Purple Analyst、👨‍🏫 Instructor、
👀 Observer / Audience。誰能看什麼、能做什麼、由什麼強制，見
[Role × UI × Permission Matrix](./architecture/role-ui-permission-matrix.md)——那份文件是
權限的單一真相來源，這裡不重複表格。

### Q2. Blue Team 有 Player 和 Instructor 嗎？

**Current.** 沒有。Blue Player 是參賽者（clearance 1，操作 Blue Portal／SOC Console）；
Instructor 是**獨立的主辦／控制角色**（clearance 3，操作 Instructor Console／Event
Control），不屬於任何一隊，兩邊的 gateway 前綴、token、UI 完全分開。

### Q3. Blue Player 在整場演練到底做什麼？

**Current.** 防守迴圈：

```text
確認接手 → 判讀技法 → 決定是否封鎖來源 → 結案
                              ↘ 標記誤報
```

（後端動作名：`acknowledge` / `classify` / `contain` / `resolve` / `dismiss`，見
[Technical Handbook §8](./technical-handbook/README.md#8-range-core)。）

兩個畫面分工不同：

- **Blue Player Portal**：個人任務 HUD——自己的兩台終端機（DMZ／內網）、團隊 KPI 摘要。
- **Blue SOC Console**：真正的工作台——alert queue、evidence、timeline、上面五個處置動作。

細節與畫面截圖見 [Participant Guide §6–§7](./participant-guide/README.md#6-blue-player-portal-使用方式)。

### Q4. Battleboard / Player Portal / Blue SOC / Purple / Instructor / Event Control 分別給誰看？

**Current.** 短答：

| 畫面 | 給誰 |
|---|---|
| Battleboard | 全場公開（教室大螢幕），任何人 |
| Red/Blue Player Portal | 對應隊伍的玩家 |
| Blue SOC Console | Blue Player（工作台）；Purple 唯讀 |
| Purple Console | Purple Analyst；Instructor 也看得到 |
| Instructor Console／Event Control | Instructor 專用 |

完整矩陣（含「由什麼機制強制」）見
[Role × UI × Permission Matrix §3](./architecture/role-ui-permission-matrix.md)。

---

## B. Red / Blue 對抗模型

### Q5. 每個 Red 都是在玩自己獨立的一份關卡嗎？

**Current（現行模型）＋ Future（下一步待標）。** 不是。目前是：

- 每個 Red 有自己的攻擊座位（一台 Kali 終端機）與**來源 IP 身分**——flag 提交、hint、
  個人分數都以來源 IP 為鍵歸屬到個人（[ADR 0003](./adr/0003-ws8-range-core-admission.md)）。
- 所有 Red 打的是**同一場 Exercise、同一個 Scenario、同一台目標主機**——不是「每人一份
  複製出來的 CTF instance」。同時間只允許一場 running exercise（同上 ADR）。

主線攻擊面下一步怎麼演進（例如 Z-BLUE 側的擴張）屬 **Future / Idea**，本 FAQ 不杜撰路線圖
細節，請看系統架構設計文件（SA）與後續 ADR。

### Q6. 不同 Scenario 可以使用不同 Target 嗎？

**Current（schema）＋ Future（多主機內容）。** `scenario/metadata.yaml` 的 attack surface
宣告本來就是清單（見 [`scenarios/shopdb-credential-pivot/metadata.yaml`](../scenarios/shopdb-credential-pivot/metadata.yaml)
的 `targets`／`surfaces`），schema 支援多主機。v1 目前只有一個 scenario、單機縱深
（一台目標主機，靠憑證鏈接形成攻擊進度，不是平台硬 gating）。

**不等於**「每個玩家一份 target clone」——同一場 exercise 裡所有 Red 打的是同一台目標，
個人歸屬靠來源 IP，不是靠複製環境。多主機、multi-service 攻擊鏈屬 **Future / Idea**。

---

## C. Exercise / Scenario / Round

### Q7. Exercise、Scenario、Target 是什麼關係？

**Current.**

```text
Exercise（一次演練的執行實例）
  └─ Scenario（打包的關卡內容，metadata.yaml 定義）
       ├─ Objectives（任務目標，含配分）
       ├─ Targets / Attack Surfaces（可攻擊的主機與服務）
       ├─ Hints / Flags
       └─ Evaluation rules（telemetry 判定或 flag 比對）
```

正式 schema 見 [Technical Handbook §8 Range Core](./technical-handbook/README.md#8-range-core)
與 [ADR 0002](./adr/0002-ws5-objective-scoring.md)。

### Q8. Scenario 是一次全部開放，還是依會場時間逐個解鎖？

**Current（scenario 內部）＋ Future / Idea（多 scenario 排程）。** 分兩層：

1. **Scenario 內部**：不靠「每 N 分鐘解鎖下一關」的計時器。目前唯一的真 scenario
   （`shopdb-credential-pivot`）靠**內容鏈接**推進——先用 SQLi 撈出資料庫憑證，
   那組憑證才打得開第二道門，flag 拿不到是因為權限邊界，不是平台故意鎖著不給打。
2. **多 Scenario 活動**：目前**沒有** Event／Round scheduler——同時間只允許一個
   prepared、一個 running exercise（[ADR 0003](./adr/0003-ws8-range-core-admission.md)
   Consequences）。若未來一場活動要排多個 scenario，屬 **Future / Idea**，還沒有票。

### Q9. 一場活動要不要等到 Round 2 才 build 下一個 Target？

**Current 做法（單場）＋ Future / Idea（多 Round 時的建議）。** 不建議等到當場才建。
目前的部署流程本來就是**先建好靶機 VM（`range-up`），再開始演練**，不是玩家進場後才
臨時起。若未來有多 Round，同樣的原則應該延伸：scenario assets 事前預建 / preload，
現場只切換 active game state——但這件事還沒有 Round scheduler 可以掛，屬 Future / Idea。

### Q10. 目前正式準備好多少個 Scenario？

**Current（會漂移，以指令為準）。** 撰寫本文當下，`scenarios/` 目錄只有一個正式 scenario：
`shopdb-credential-pivot`。這個數字**不是**這裡的權威來源——真正的清單請直接查：

```bash
ls scenarios/
```

`config/scenario-sources.yaml` 裡另外列的 `sqli-01`／`bruteforce-01`／`falco-*` 是驗證
pipeline 用的**測試載具（fixture）**，不是正式 scenario，兩者在 registry 裡刻意分區塊，
不要混進場次數量。

---

## D. 計分

### Q11. Red 怎麼計分？

**Current.** 個人分數，來源 IP 是名冊的鍵：

- **Objective 完成**：靠 telemetry 自動判定或玩家提交比對，各自帶分數（`shopdb-credential-pivot`
  範例：偵測到 SQLi 100 分、拿到 flag 200 分——**這是這個 scenario 自己的配分，不是平台
  固定規則**，別的 scenario 可以不一樣）。
- **Flag 提交**：答對更新分數，答錯可再試。
- **Hint 扣分**：同一個 Objective 疊多個 hint 時**取最大扣分比例，不是累加**
  （[ADR 0002](./adr/0002-ws5-objective-scoring.md) ④）。
- 支援個人排名（`GET /api/score` 的 `red.players[]`）。

### Q12. Blue 怎麼計分？

**Current.** 團隊層級，不是個人排名——不同 Blue segment 面對的攻擊量本來就不一樣，直接套
Red 的 individual leaderboard 沒有意義（[Participant Guide §8](./participant-guide/README.md#8-flag--hint--score-規則)）。
計分基礎是五個處置動作（Q3）：`acknowledge` / `classify`（判讀技法） / `contain`（封鎖，
**沒有真的派送成功就不計分**） / `resolve` / `dismiss`。配分參數在
`config/blue-scoring.yaml`（`detect_attack` / `identify_technique` / `contain` /
`resolve_incident` 四個分項），`GET /api/score` 的 `blue` 欄位就是這個計算結果——
這不是待定公式，是已經在跑的路徑。

### Q13. Purple / Instructor 有分數嗎？

**Current.** 沒有。`GET /api/score` 的回應只有 `red` 與 `blue` 兩個 key。Purple 的角色是
evaluation（涵蓋率、偵測延遲、証據完整度），Instructor 是 exercise control——兩者都不是
參賽計分角色。若日後 spec 有不同決定，以那份 spec 為準。

---

## E. Client / Deployment

### Q14. 玩家端需要安裝 Kali、Docker、VM、agent 嗎？

**Current.** 不需要。Participant client 是 **browser-first、zero-install**：終端機是
瀏覽器裡的 `<iframe>`（ttyd 代理），攻防工具與 seat runtime 全部跑在 Cyber host／range
環境，不在玩家自己的機器上（[Technical Handbook §5 UI Architecture](./technical-handbook/README.md#5-ui-architecture)）。

真正的 client 前提：現代瀏覽器、JavaScript／Cookie／WebSocket 可用、網路連得到部署
主機。精確版本要求以 [Operator Guide](./operator-guide/README.md) 部署章節為準。

### Q15. `bootstrap.sh` 是玩家要跑的嗎？

**Current.** 不是。`bootstrap.sh`／`deploy.sh` 是 **Operator（部署主機的工作人員）**的
安裝入口，參賽者不應該、也不需要碰它。部署首次執行的體驗（進度、完成摘要、URL 導引）
見 [#144 Bootstrap First-Run UX](https://github.com/Graylee0128/cyber/issues/144)；
操作步驟見 [Operator Guide](./operator-guide/README.md)。

### Q16. 未來 Red AI 會要求玩家自己下載小模型嗎？

**Future / Idea.** 不會，這不是目前的產品方向。已記錄的方向（見
[#131](https://github.com/Graylee0128/cyber/issues/131) comment，尚未開實作票）：

```text
Seat-local AI  >  Central server AI  >>>  Client-side installed model
```

理由：zero-install（呼應 Q14）、玩家間的公平性、硬體差異、模型下載量、WebGPU／瀏覽器
相容性、可稽核性。可能的 AI Assistance Policy 分級：`Disabled` / `Hint Only` / `Guided` /
`Full Copilot`——Training Mode 可以較開放，Assessment／Competition Mode 應能關閉或限制。
**這整段是方向記錄，不是已實作功能**；現有已落地的 AI（Exercise Report 敘事、
Instructor SOC Copilot）跑在伺服器端，見
[Technical Handbook §10 AI Assistance](./technical-handbook/README.md#10-ai-assistance)。

---

## See also

- [Participant Guide](./participant-guide/README.md) — 玩家怎麼參加
- [Operator Guide](./operator-guide/README.md) — 工作人員怎麼把它跑起來
- [Technical Handbook](./technical-handbook/README.md) — 系統現在怎麼運作
- [Role × UI × Permission Matrix](./architecture/role-ui-permission-matrix.md)
- [ADR 0002 — WS5 Objective & Scoring](./adr/0002-ws5-objective-scoring.md)
- [ADR 0003 — WS8 Range Core ＋ Admission](./adr/0003-ws8-range-core-admission.md)
