# Spec — WS8 Event Control Plane（會議中控）

- **Status**: draft（尚無票消費，定案後比照 P1 遷出至 `docs/`；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-12 對照外部參考架構（Metis 紅藍對抗環境，不在本 repo）＋ 兩題拍板（藍隊形態、座位規模）
- **上位文件**：[SA](../../資安攻防平台_系統架構設計文件_v0.1.md) §4.2、§5.5、§12
- **上游依賴**：[WS1 spec §1.3](../ws1-game-design/spec.md)（個人計分）、[WS5 spec §2.1／§2.3](../ws5-range-core/spec.md)（PG 慣例、權限邊界）
- **下游影響**：WS6（動態配置）、WS3（藍隊動作）、WS7（Player Portal 嵌入 shell）
- **草案拓樸圖**：[topology-v0_3-draft.svg](./topology-v0_3-draft.svg)
- **中控畫面 demo**：[demo.html](./demo.html)（零依賴單檔、資料寫死；視覺提案，不代表 WS8 開工）
- **玩家旅程圖**：[player-journey-v0_1-draft.svg](./player-journey-v0_1-draft.svg)
  —— 七階段 × 六泳道，本檔 §2／§3／§4 的時間軸版本。**這是全 repo 第一張流程圖**（既有六張 SVG 全是拓樸／分層，畫的是空間不是時間）

---

# 0. 為什麼這是架構必需，不是活動工具

[WS1 spec §1.3](../ws1-game-design/spec.md) 已定案「Red 分個人計分」，WS5 的 Exercise State
因此需要 `player_id`／`source_ip` 欄位。

**但目前沒有任何元件在產生 `player_id`。** 六台 kali 由
[zones.env](../../scripts/range/zones.env) 靜態配置，架構裡不存在「誰坐 kali-03」這個概念。
個人計分的資料結構已經定了，資料來源卻是空的。

WS8 就是那個來源。它不是為了 demo day 好看而存在 —— 它是「人 ↔ 機器 ↔ 分數」這條綁定的
唯一產生點。沒有它，WS1 §1.3 與 WS5 的 schema 都只是宣告。

---

# 1. 定位與邊界

| | 內容 |
|---|---|
| **擁有** | 身分（憑據 → 人）、座位（人 → 機器）、會話（瀏覽器 → 座位） |
| **不擁有** | Exercise state／score／objective（WS5）、telemetry／detection（WS4）、容器生命週期（WS6） |

WS8 在領號完成的那一刻把 `player_id` 交給 Range Core，然後就退出。
**它不得成為第二個遊戲狀態的真相來源** —— 那是 SA §3.2 對 Grafana 講過的同一句話。

---

# 2. 三段分離：憑據 ／ 座位 ／ 會話

## 2.1 憑據只證明「可以入場」，不證明「你是誰」

| 憑據 | 唯一性 | 能不能識別人 |
|---|---|---|
| 現場進場碼（HMAC 時間窗，60s 更新） | **全場共用** | ❌ 結構上做不到 |
| 遠端一次性連結 | 每人一份 | ✅ |

**因此身分在領號時鑄造，不在驗證時。** 驗證的輸出只有一個 boolean（可否入場）；
`player_id` 是配到座位那一刻才誕生的。

**為什麼要把這兩件事拆開**：合併會讓現場路徑無法實作 —— 全場一百個人輸入同一組進場碼，
驗證器沒有任何資訊可以區分他們。把身分綁在憑據上的設計，在現場路徑上直接壞掉。

## 2.2 回程 key 是 session token，不是憑據

外部參考架構的流程圖標「同一憑據 · 換裝置／重整都回到同一套」。
**這對共用進場碼不成立** —— 憑據對所有人都一樣，回程時無從對應。

實際的回程 key：

| 路徑 | 回程靠什麼 | 換裝置 |
|---|---|---|
| 遠端一次性連結 | 連結本身（每人唯一） | 可以 |
| 現場進場碼 | **session cookie** | **不行** |

**直接後果**：現場玩家清掉 cookie／換一台筆電就回不去自己的座位。
因此必須有一個 **instructor 手動釋放／改綁座位**的操作（見 §7 WS7）。
這不是可選的便利功能，是現場路徑的必要補償。

## 2.3 三段的生命週期與失效語意

| 段 | 存活期間 | 失效條件 |
|---|---|---|
| 憑據 | 進場碼：60s 時間窗；連結：一次 | 過期／用過 |
| 座位 | 整場演練 | 演練結束、instructor 釋放 |
| 會話 | 瀏覽器 cookie | 逾時、登出、instructor 改綁 |

三者失效互不連動：session 過期不釋放座位（人只是關了分頁）；
座位釋放一定使 session 失效（機器已經給別人了）。

---

# 3. 座位池

## 3.1 用 PostgreSQL，不用 sqlite

外部參考架構用 sqlite。本專案用 PostgreSQL。

**為什麼**：

1. repo 已經強制 PG 且**刻意不分層**（[README](../../README.md)：「測試一律需要 PostgreSQL，
   不分層 —— 才不會有本機綠、CI 紅」）。引入第二種資料庫等於多一套備份、遷移、測試起法。
2. 原子配位用 `SELECT … FOR UPDATE SKIP LOCKED`，是 PG 的既有能力，不需要應用層自旋鎖。
3. [WS5 spec §2.1](../ws5-range-core/spec.md) 已經在用 PG 的 partial unique index 在資料庫層
   強制「同時只有一場 running」。座位池是同一種模式的第二個實例，不是新機制。

## 3.2 無候補、不共用機器 —— 這是被計分模型強制的，不是 UX 選擇

額滿即止，沒有候補佇列，不做「兩人輪流用一台」。

**為什麼不是 UX 偏好**：[WS1 spec §1.3](../ws1-game-design/spec.md) 的個人計分，靠的是
Z-RED 六台 kali 各自獨立、**不可被 SNAT 塌縮**的來源 IP（這也是 G0 世代的退場理由，SA §12.1）。
兩個人共用一台 kali，來源 IP 就不再識別人 —— 個人計分不是變難，是直接失效。

所以「不共用」是計分模型的物理前提。候補佇列只在「有人會離開讓出機器」時才有意義，
而演練是純時間制、全場同時結束（[WS1 spec §5.1](../ws1-game-design/spec.md)），
沒有中途讓位這回事。

## 3.3 座位是 team-agnostic 的，紅藍分開計池

```
seat {
  seat_id, exercise_id,
  team        -- red | blue
  kind        -- shell | console
  endpoint    -- 由 provisioner 回寫（IP／ttyd upstream）
  state       -- free | requested | ready | claimed | released
  player_id   -- 配位時鑄造
  claimed_at
}
```

`kind` 讓「藍隊拿受限 shell」與「藍隊只有 console」兩種形態共用同一張表。
本次已拍板走 `shell`（§6），但欄位保留 —— 未來若有只給 console 的角色（如觀察員），
不需要改 schema。

---

# 4. 動態配置：中控不建容器

拍板規模為 **50+ 人**，靜態預配不成立（開場前跑一次 `range-up.sh` 建好所有容器，
在 50+ 規模下會讓開場前置時間與資源佔用都失控），必須改為領號時建立。

## 4.1 座位由 host 側 provisioner agent 以 **pull 模式**建立

```
中控寫入 seat(state=requested)
        ↓  （provisioner 輪詢，中控不主動呼叫）
host 側 Seat Provisioner Agent
        ↓
建立容器／網路 → 回寫 endpoint、state=ready
```

**為什麼不讓中控直接建容器**：[WS5 spec §2.3](../ws5-range-core/spec.md) 已經拒絕過同型的
權限擴張 ——「API 直接驅動 WS6 腳本，等於 Z-APP 的服務需要操作 host 上 OVS／libvirt 的權限，
是明顯的權限擴張」。中控的暴露面比 Range Core 更大（它面對匿名網際網路），
理由只會更強，不會更弱。

**為什麼是 pull 不是 push**：與契約 2（`TARGET → MGMT` 單向、response 走 agent pull）
同一條邏輯。push 模式會讓網路平面上的服務成為「可主動對 host 下指令」的來源。
延遲換取單向性，這是刻意的取捨，不是效能疏忽。

## 4.2 這把 WS6 從「開場前跑一次」改成「常駐配置服務」

**這是 50+ 規模的真正代價，不是 WS8 的附屬品。**
`range-up.sh` 目前是一次性 IaC 腳本；provisioner agent 是長駐服務，需要自己的
健康檢查、失敗重試、孤兒容器回收。應獨立切票，歸屬 WS6。

WS8 對它的唯一要求是那張 seat 表的契約（`requested` → `ready` → `released`），
provisioner 怎麼實作不在本檔範圍。

---

# 5. 網路：Z-EDGE 與第五條契約

## 5.1 Z-EDGE 是一個區，不是防火牆前的一台機器

外部參考架構把入口 VM 畫在防火牆**前面**。本專案的防火牆同時是 inter-VLAN gateway，
入口若在它前面就不受任何 inter-VLAN 政策管轄。

因此 Z-EDGE 是 **VLAN 50**，掛在同一個防火牆下，向內流量一律過政策。

## 5.2 契約 5：`EDGE → MGMT` deny all

**理由不是「多一條比較安全」**：Z-EDGE 是唯一「**未通過任何驗證**的流量碰得到」的區。
Z-RED 裡至少是已經入場的人；Z-EDGE 面對的是匿名網際網路。把它放得比 Z-RED 寬鬆是反的。

連帶新增 `EDGE → TARGET` deny all ——中控完全不需要碰靶機，入口被打下不該附贈一條
到攻擊面的捷徑。

**這是破壞性變更等級的新增**：SA §12.2 的四條契約變成五條，
任何一條變動都要複查 Purple Platform 全層。

## 5.3 Z-EDGE 不持久化任何東西

外部參考架構把 sqlite 放在入口 VM 上。本專案不這樣做。

| 元件 | 住哪 | 為什麼 |
|---|---|---|
| nginx／TLS 終結、反向代理 | Z-EDGE | 必須在對外那一側 |
| 憑據驗證、領號、session ↔ seat、`player_id` 鑄造 | **Z-APP**（Admission Service） | Z-EDGE 被打下時不該連帶交出座位表與 HMAC secret |

Z-EDGE 透過 `EDGE → APP :443` 向 Admission Service 查驗（nginx `auth_request` 子請求）。
它自己只有 TLS 憑證與反代設定，**沒有資料庫連線、沒有座位狀態**。

**代價**：每個請求多一跳。在 50+ 併發、且熱路徑只有領號那一次的規模下，可接受。

## 5.4 ttyd 跑在容器內，不在 host 上

外部參考架構寫「ttyd process 跑在 VM 上」。本專案不能照抄：
host 的 mgmt NIC 在 Z-MGMT（`.21`／`.22`），Z-EDGE 反代過去即構成 `EDGE → MGMT` ——
**直接撞契約 5**。

因此 ttyd 跑在每個 seat 自己的容器內，綁該容器的區內 IP，政策為 `EDGE → RED :7681`
（藍隊同理，`EDGE → BLUE :7681`）。

**副作用（可接受）**：玩家在自己的容器內有權限，可以殺掉自己的 ttyd。
爆炸半徑僅限自己的座位，且他本來就擁有那台機器。

## 5.5 對外發布方式是部署選項，不是架構

架構只要求「單一 `:443` 入口落在 Z-EDGE」。公網 IP、反向隧道（雙實例互備）、場館 VPN
三種都滿足，選哪一種不改變本檔任何一條政策。

若採反向隧道，Z-EDGE 需要對外 egress —— 應限制為只到隧道端點的 allowlist，
不是整段開放。

---

# 6. Z-BLUE（VLAN 60）—— 本次拍板

## 6.1 藍隊拿受限 shell，不是 root

藍隊每人一段：對外主機 a（DMZ）＋ 內部主機 b（flag）。
玩家拿**受限帳號 ＋ sudo allowlist**，不是 root。

## 6.2 遙測路徑不在 allowlist 內

sudo allowlist **不得包含**：Falco／Alloy 的設定檔與 systemd unit、其所在目錄的寫入權、
以及任何能停用它們的手段。

**為什麼這條是硬約束**：契約 4 說 collector 裝在 target 側。若被評分的人能關掉 collector，
`action coverage` 的分母就由被評分者自己決定 —— 數字失去公信力。
給藍隊 root 等於把遙測的完整性交到被評分的人手上，那不是「比較刺激」，是量測失效。

## 6.3 Z-BLUE 與 Z-TARGET 是兩件事

| | Z-TARGET（VLAN 20） | Z-BLUE（VLAN 60） |
|---|---|---|
| 誰有 shell | **沒有人** | 藍隊玩家（受限） |
| 遙測可信度 | **結構性可信**（無人可碰） | 約定式可信（靠 allowlist） |
| 擁有者 | Scenario（WS2），隨關卡重置 | Seat（WS8／WS6），隨座位重置 |
| 數量 | 共用一台 | 每人一段 |
| 紅隊政策 | `:80 :3306` 全開，保底攻擊面 | 只到 DMZ；內部主機須橫向移動 |

**為什麼不併成一區**：P2 的 coverage／MTTD 分母長在 Z-TARGET 上。它是唯一「沒有任何玩家
碰得到」的遙測基準線 —— 「偵測管線自己還活著嗎」這個問題只能拿它量。
Z-BLUE 上的每個數字都帶一句隱含前提「假設 allowlist 沒被繞過」；
把兩者混在一起，那句前提會污染所有既有的 P2 指標。

**次要理由**：現行 T4 全鏈測試（`tests/integration/test_falco_range_chain.py`）全部跑在
`web-target` 上。合併等於同時廢掉既有的驗證證據。

## 6.4 每人隔離做在容器網路層，不是 VLAN 層

50+ 人若一人一個 VLAN，管理上不可行。
每個 seat 一個獨立容器網路（外部參考架構的 `172.31.<X>.0/24` 作法）＋ `DOCKER-USER` DROP，
同區內玩家彼此打不到。

**同一條規則也要補進 Z-RED** —— 現行六台 kali 之間是否互通沒有明文規定，
也沒有測試在管。50+ 規模下這會變成「紅隊互相干擾對方的計分」。

收尾期由 instructor 開放互打，是政策切換，不是預設。

## 6.5 對已定案文件的影響（必須回頭改）

| 文件 | 原本 | 現在 |
|---|---|---|
| [WS1 spec §1.3](../ws1-game-design/spec.md) | 「Blue 側不做個人化」 | **失效** —— 藍隊有自己的機器，需要 `player_id` |
| [product-ui spec Q5](../product-ui/spec.md) | 「Blue 的 Portal 很薄，要不要不做」 | **有內容了** —— 嵌入 shell ＋ 自己那段的狀態 |
| SA §12.3 | 「Blue 不取得 Z-MGMT 底層存取權」 | **仍然成立** —— Z-BLUE 不是 Z-MGMT |
| P2 Action Registry（隊級） | 維持隊級 | **維持不變** —— 藍隊偵測能力仍是全場級指標 |

---

# 7. 與既有 workstream 的接縫

| WS | 接縫 | 方向 |
|---|---|---|
| **WS5** Range Core | Admission Service 住 Z-APP；領號成功後鑄造 `player_id` 交給 Range Core | WS8 → WS5 |
| **WS6** Range Infra | seat 表契約（`requested`／`ready`／`released`）；provisioner agent 屬 WS6 | WS8 → WS6 |
| **WS7** Product UI | Player Portal 嵌入 shell（回答 [product-ui Q4](../product-ui/spec.md) 的技術選型與隔離）；Instructor Console 需新增「釋放／改綁座位」 | WS8 → WS7 |
| **WS4** Purple | 不接。中控不產生也不消費 Core Event | 無 |
| **WS3** Blue Ops | 藍隊動作現在有落地的機器可執行 | WS8 → WS3 |
| **WS1** Game Design | §1.3「Blue 不個人化」需回改 | WS8 → WS1 |

---

# 8. 對 SA 的影響（要回寫的）

| SA 章節 | 現況 | 需要改成 |
|---|---|---|
| §4.2 | 「WS4 是唯一跨區的 workstream」 | 不再成立 —— WS8 跨 `EDGE → APP` |
| §12.2 | 四條跨世代契約 | 五條（新增 `EDGE → MGMT` deny all） |
| §12.1 | 世代表 G0／G1／G2／G3(k8s) | 新增 G3＝六區＋中控；k8s 順延為 G4 |
| §12.4 | 「VLAN 數量（G2 定為四區）已不再是未決事項」 | 重新開啟 —— 四區 → 六區 |
| §4 | 七個 workstream | 八個（新增 WS8 Event Control Plane） |

---

# 9. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼不在本檔決定 |
|---|---|
| ttyd 的具體前端（xterm.js 版本、字型、複製貼上行為） | 實作層細節，不影響任何 schema 或跨區政策 |
| 操作側錄的格式 | 與 [product-ui Q3](../product-ui/spec.md)（Instructor 稽核）是同一類問題，應一起決定，不在本檔單獨定 |
| provisioner 的實作（docker API／腳本／operator） | 屬 WS6。WS8 只定 seat 表契約 |
| 進場碼的字元集與長度 | 實作層細節 |
| Z-BLUE 的 sudo allowlist 具體條目 | 屬 WS3（藍隊動作的封閉列舉）；本檔只定「遙測路徑不得在內」這條約束 |
| 觀察員／來賓唯讀席 | `seat.kind` 已預留，實際要不要做屬 WS1 |
| 多場演練並行 | [WS5 spec §2.1](../ws5-range-core/spec.md) 已定同時一場，SA §16 歸 V3 |

---

# 10. 決策總表

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 1 | 中控是不是架構必需 | 是 —— `player_id` 的唯一產生點 | §0 |
| 2 | 憑據與身分的關係 | 分離；身分在領號時鑄造，不在驗證時 | §2.1 |
| 3 | 回程靠什麼 | session token；現場路徑需 instructor 手動改綁補償 | §2.2 |
| 4 | 座位池資料庫 | PostgreSQL（`FOR UPDATE SKIP LOCKED`），不用 sqlite | §3.1 |
| 5 | 額滿怎麼辦 | 無候補、不共用 —— 個人計分的物理前提 | §3.2 |
| 6 | 誰建容器 | host 側 provisioner agent，**pull** 模式 | §4.1 |
| 7 | Z-EDGE 的地位 | 一個區（VLAN 50），不是防火牆前的一台機器 | §5.1 |
| 8 | 新契約 | 契約 5：`EDGE → MGMT` deny all（＋`EDGE → TARGET` deny） | §5.2 |
| 9 | 狀態住哪 | Z-EDGE 零持久化；領號與身分住 Z-APP | §5.3 |
| 10 | ttyd 跑在哪 | 容器內，綁區內 IP —— 跑 host 會撞契約 5 | §5.4 |
| 11 | 藍隊形態 | 受限 shell ＋ Z-BLUE（VLAN 60），非 root | §6.1 |
| 12 | 遙測完整性 | sudo allowlist 不得含遙測路徑 | §6.2 |
| 13 | Z-BLUE vs Z-TARGET | 兩區並存，差別在「誰有 shell」與遙測可信度 | §6.3 |
| 14 | 每人隔離做在哪一層 | 容器網路層 ＋ DOCKER-USER DROP，不是 VLAN 層 | §6.4 |
| 15 | 座位規模 | 50+，動態配置 | §4 |

---

# 11. 待決（需另行拍板，本檔不預設）

1. **Z-BLUE 的靶機鏡像從哪來** —— 沿用 `build-golden-target.sh` 的 golden image，
   還是另烤一份？沿用可省事，但 golden 目前是「單一共用靶」的假設。
2. **紅隊打的是 Z-TARGET 還是 Z-BLUE** —— 兩個攻擊面同時存在時，scenario 要指定主線，
   否則 objective 判定會有兩個可能來源。屬 WS2。
3. **50+ 規模下的實體承載** —— 單主機撐不撐得住 50+ 容器 ＋ 每人一個 ttyd？
   需要實測，可能逼出多主機，那會動到 OVS trunk 的設計。
