# Spec — WS8 Event Control Plane（會議中控）

- **Status**: 已定案（#65 decision gate 於 2026-08-12 拍板收斂，25 條決策；由 canonical #59／#62 消費；比照 P1 遷出至 `docs/` 待排；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-12 對照外部參考架構（Metis 紅藍對抗環境，不在本 repo）＋ 兩題拍板（藍隊形態、座位規模）
  ＋ 同日第二輪 grilling 十題（#65 decision gate，決策 16–25）
- **上位文件**：[SA](../../資安攻防平台_系統架構設計文件_v0.1.md) §4.2、§5.5、§12
- **上游依賴**：[WS1 spec §1.3](../ws1-game-design/spec.md)（個人計分）、[WS5 spec §2.1／§2.3](../ws5-range-core/spec.md)（PG 慣例、權限邊界）
- **下游影響**：WS6（動態配置）、WS3（藍隊動作）、WS7（Player Portal 嵌入 shell）
- **拓樸圖**：[demo_network_topology_v0_3.svg](../../demo_network_topology_v0_3.svg)（2026-08-13 升正，取代 v0.2.1；原草案由本 workstream 產出）
- **中控畫面 demo**：[demo.html](./demo.html)（零依賴單檔、資料寫死；視覺提案，不代表 WS8 開工）
- **玩家旅程圖**：[player-journey-v0_1-draft.svg](./player-journey-v0_1-draft.svg)
  —— 七階段 × 六泳道，本檔 §2／§3／§4 的時間軸版本。流程圖 A。
  姊妹圖 B（單次攻防的跨元件時序）在 [attack_chain_sequence_v0_1.svg](../../attack_chain_sequence_v0_1.svg)，
  屬 WS4／P1 已實作範圍，不在本 workstream

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

**憑據也不帶隊伍資訊**（決策 16）。曾經考慮過「紅一組進場碼、藍一組」讓憑據多帶 1 bit，
但拍板改為**玩家在領號畫面自選紅／藍**，憑據因此維持純 boolean 輸出。
連帶後果：領號 API **接受**客戶端指定 `team`，所以池上限的檢查必須在伺服器端做（§3.2），
不能靠「客戶端不會亂送」這個假設。

## 2.2 回程 key 是 session token，不是憑據

外部參考架構的流程圖標「同一憑據 · 換裝置／重整都回到同一套」。
**這對共用進場碼不成立** —— 憑據對所有人都一樣，回程時無從對應。

實際的回程 key：

| 路徑 | 回程靠什麼 | 換裝置 |
|---|---|---|
| 遠端一次性連結 | 連結本身（每人唯一） | 可以 |
| 現場進場碼 | **session cookie** | **不行** |

**直接後果**：現場玩家清掉 cookie／換一台筆電就回不去自己的座位。
因此必須有 **instructor 手動介入座位**的操作（§7.1 拆成「重綁 session」與「釋放座位」兩個）。
這不是可選的便利功能，是現場路徑的必要補償。

## 2.3 三段的生命週期與失效語意

| 段 | 存活期間 | 失效條件 |
|---|---|---|
| 憑據 | 進場碼：60s 時間窗；連結：一次 | 過期／用過 |
| 座位 | 整場演練 | 演練結束、instructor 釋放 |
| 會話 | 瀏覽器 cookie | 逾時、登出、instructor 重綁 session（§7.1） |

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

## 3.2.1 玩家自選隊伍，池上限由 instructor 設定

隊伍**由玩家在領號畫面自選**（決策 16）。自選會逼出一個靜態配置時不存在的需求：
**每池必須有席位上限**。憑據決定隊伍時，發幾張碼就是幾個人；自選且無上限時，
全場會一面倒進紅池，藍隊湊不滿，演練跑不起來。

| 項目 | 定案 |
|---|---|
| 上限住哪 | exercise 設定（如 `red=30` / `blue=20`），開場前由 instructor 設 |
| 誰檢查 | **伺服器端**。領號 API 對該池計數，用 `FOR UPDATE SKIP LOCKED`（§3.1）|
| 額滿時 UI | 該隊在選單上**反灰不可選**，不是選了才報錯 |

**為什麼是反灰不是報錯**：50+ 同時湧入時，「選了才報錯」會產生大量重試與重複的池計數查詢。
反灰是唯一不需要「退回選單」流程的做法。代價是玩家會看到自己想選的隊伍不能選，
這是自選方案本來就要付的體驗成本。

**紅藍兩池的上限語意不同**（見 §4.3）：紅池上限是「最多可以動態長到幾個座位」，
藍池上限是「開場時要預先建幾段」。

## 3.3 座位是 team-agnostic 的，紅藍分開計池

```
seat {
  seat_id, exercise_id,
  team        -- red | blue   （由玩家領號時自選，§3.2.1）
  kind        -- shell | console
  endpoints   -- 由 provisioner 回寫。紅隊一個；藍隊兩個（a=DMZ、b=flag，§5.4）
  state       -- free | requested | ready | claimed | failed | released
  player_id   -- 於 requested 鑄造（§4.4）
  claimed_at
}
```

`kind` 讓「藍隊拿受限 shell」與「藍隊只有 console」兩種形態共用同一張表。
本次已拍板走 `shell`（§6），但欄位保留 —— 未來若有只給 console 的角色（如觀察員），
不需要改 schema。

`endpoints` 是複數（原為單數 `endpoint`）：藍隊一段是**兩台機器**，各自一個 ttyd
（§5.4）。紅隊仍只有一個，但欄位形狀統一，避免兩種 seat 走兩套讀取路徑。

`failed` 是動態配置逼出來的新狀態 —— 靜態預配時容器在開場前就建好了，沒有
「配位成功但環境起不來」這件事。語意與逾時處置見 §4.4。

---

# 4. 座位配置：中控不建容器

拍板規模為 **50+ 人**。全量靜態預配不成立（開場前跑一次 `range-up.sh` 建好所有容器，
在 50+ 規模下會讓開場前置時間與資源佔用都失控）。

但**動態配置不是全域規則** —— 紅池動態、藍池預建，兩者的建置時機不同（§4.3）。
共用的鐵律只有一條：不論何時建，**建容器的都不是中控**（§4.1）。

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

WS8 對它的唯一要求是那張 seat 表的契約（`requested` → `ready` → `released`，
失敗時 → `failed`），provisioner 怎麼實作不在本檔範圍。

## 4.3 紅池動態、藍池預建 —— 因為藍隊的機器同時是紅隊的攻擊面

主線攻擊面拍板為 Z-BLUE（§6.6）。這讓「領號時才建容器」對藍池直接失效：

**藍隊的機器就是紅隊要打的目標。** 若藍隊 seat 等人領號才建，紅隊的攻擊面會隨著
藍隊員陸續報到而**一台一台冒出來** —— 早到的紅隊員可打的目標比晚到的少。
而選 Z-BLUE 當主線的理由，正是「每個人的攻擊面等價」（§6.6）。動態配置會把那個理由吃掉。

| | 紅隊 seat | 藍隊 seat |
|---|---|---|
| 是不是攻擊面 | 否 | **是** |
| 沒人坐時 | 不存在也沒差 | **必須存在**，否則攻擊面缺一塊 |
| 建置時機 | 領號時（`requested` → `ready`） | **exercise start 時一次全建** |
| 中途可否領號 | **可以** —— 晚到只是自己時間變少 | **不可以**，start 即鎖池 |

**藍池的鎖點是 exercise start**（決策 25）。instructor 在開場前依**實到人數**設定藍池上限，
建幾段就是幾人，因此不存在無人防守的空段。start 之後藍隊不再開放自助領號；
換人、換裝置一律走 §7 的 instructor 手動流程。

**為什麼不留空段給遲到的人**：無人防守的 Z-BLUE 段是**送分靶** —— 紅隊打下來拿滿分，
而那段從頭到尾沒有防守方。留著它就是在計分裡塞一個與能力無關的變數。

**代價**：instructor 在開場前必須確定藍隊實到人數（點名或看報到），這是現場流程負擔。
且卡在路上的藍隊員那段不存在，只能事後手動補建 —— 而補建會讓攻擊面在演練中途變化，
正是本節要避免的事。這個代價是刻意接受的：手動補建是可控的例外，
自助領號造成的攻擊面漂移是常態。

## 4.4 `player_id` 在 `requested` 鑄造，`ready` 才交給 Range Core

靜態預配時，「配到座位」與「座位可用」是同一刻。改成動態配置後，兩者中間隔著
provisioner 的輪詢延遲，`player_id` 必須明確落在其中一邊。

**定案：`requested` 時鑄造，延到 `ready` 才交給 Range Core。**

**為什麼不等 `ready`**：玩家在等待期間必須已經有身分，否則 session cookie 沒東西可綁 ——
而 §2.2 已經定了現場路徑**只能**靠 session cookie 回程。等待中重整頁面若要重新領號，
等於在最脆弱的時刻把人踢回起點，還會在池上留下一個懸空的 `requested` 座位。

**為什麼不立刻交出**：Range Core 會拿到永遠不會 `ready` 的 `player_id`（容器建失敗時），
必須反向撤銷。延到 `ready` 交出，失敗路徑就不會污染 Range Core 的狀態。

逾時處置：

1. `requested` 超過 **T 秒** → 標 `failed`，provisioner 自動重試一次
2. 再失敗 → 釋放座位、`player_id` 作廢、玩家回領號畫面，**中控同時告警給 instructor**
3. **T 的值由承載 spike 量出的「容器啟動到 ready」時間決定**（[#78](https://github.com/Graylee0128/cyber/issues/78) 實測）：

   容器啟動到**網路掛載完成**的時間，在 6→150 台這個區間穩定在 **~0.5 秒/台**（線性）。
   這量的是 `attach-red.sh` 的 `sleep infinity` 容器接上 OVS 的時間，**不是完整服務就緒**——
   沒有真正的 seat 應用程式（ttyd、實際環境）要初始化，所以 **0.5 秒是 T 的下限，不是終值**。
   #62 的 seat provisioner 落地、用真容器重量一次後，數字只會更大，不會更小；本檔仍不
   預設最終數字，理由不變（避免拿模擬值頂替真容器的量測）。

已知代價：Range Core 會看到一個「已鑄造但尚未報到」的 `player_id` 空窗期。
紅隊中途領號時該玩家的計分起點就是 `ready` 那一刻 —— 純時間制下這是晚到者自負的結果
（[WS1 spec §5.1](../ws1-game-design/spec.md)），不做正規化補償。

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

> **2026-08-15 補記（#126 item 4）**：本節的「Z-EDGE 零憑證」是硬約束，
> `tests/deploy/test_edge_access.py::test_edge_config_contains_no_database_or_credential_material`
> 是它的看守者。名冊來源 IP 的代宣告因此**不放在 Z-EDGE**（那需要握有 Range Core 的
> 服務 token），改由本來就持有全部服務 token 的 Product UI gateway 承擔 ——
> 見 [ADR 0004](../../docs/adr/0004-roster-attribution-via-trusted-gateway.md)。

## 5.4 ttyd 跑在容器內，不在 host 上

外部參考架構寫「ttyd process 跑在 VM 上」。本專案不能照抄：
host 的 mgmt NIC 在 Z-MGMT（`.21`／`.22`），Z-EDGE 反代過去即構成 `EDGE → MGMT` ——
**直接撞契約 5**。

因此 ttyd 跑在每個 seat 自己的容器內，綁該容器的區內 IP，政策為 `EDGE → RED :7681`
（藍隊同理，`EDGE → BLUE :7681`）。

**副作用（可接受）**：玩家在自己的容器內有權限，可以殺掉自己的 ttyd。
爆炸半徑僅限自己的座位，且他本來就擁有那台機器。

### 藍隊一段兩個 ttyd，不走跳板

seat 是「一段」不是「一台」：藍隊一段有對外主機 a（DMZ）與內部主機 b（flag，§6.1）。
**兩台各跑一個 ttyd**，Player Portal 上呈現為兩個 terminal 分頁。

**為什麼不讓 b 從 a `ssh` 過去**（那更像真實跳板流程，也少一個 ttyd）：
**紅隊拿下 a 的那一刻，藍隊會同時失去對 b 的存取。** 攻擊方的成功直接沒收防守方的工具，
而 WS3 的 investigation 與封鎖動作全部需要能登入機器 —— 這不是增加難度，是讓被打的人無法反應。

**為什麼不加管理跳板容器**（邊界最乾淨：管理面與被攻面分離）：20 人等於多 20 個容器。
在承載尚未實測（§11）前不先付這個成本。若 spike 顯示資源寬裕，這是日後值得回頭做的改良。

連帶：`EDGE → BLUE :7681` 的政策必須涵蓋**一段內兩台**的區內 IP，Portal 要管兩條 WebSocket。

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
| collector 位置 | target 側（golden VM 內） | **host 層**，玩家碰不到（§6.7） |
| 遙測基準線 | **乾淨**（無人操作，只有攻擊流量） | 有防守方操作雜訊 |
| 擁有者 | Scenario（WS2），隨關卡重置 | Seat（WS8／WS6），隨座位重置 |
| 數量 | 共用一台 | 每人一段 |
| 紅隊角色 | 保底攻擊面／暖身，**不進個人計分** | **主線**，個人計分 objective（§6.6） |
| 紅隊政策 | `:80 :3306` 全開 | 只到 DMZ；內部主機須橫向移動 |

**為什麼不併成一區**：P2 的 coverage／MTTD 分母長在 Z-TARGET 上。它是唯一「沒有任何玩家
碰得到」的區 —— 「偵測管線自己還活著嗎」這個問題只能拿它量，因為它上面的所有活動
都是攻擊活動，沒有防守方的正常操作混在裡面。

> **本節論證已於 #65 修訂。** 原本的理由是「Z-BLUE 的遙測是約定式可信，
> 每個數字都帶『假設 allowlist 沒被繞過』的前提」。§6.7 把 collector 移到 host 層之後，
> 玩家在容器內結構上碰不到遙測，**這條理由不再成立** —— Z-BLUE 現在也是結構性可信的。
> 兩區仍然分開，但理由換成上面那條：**基準線要的是「沒有人在上面工作」，不是「沒有人能關掉 collector」**。
> §6.2 的 allowlist 約束**保留**，但它從唯一防線降級為第二層防線。

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
| [WS1 spec §1.3](../ws1-game-design/spec.md) | 「Blue 側不做個人化」 | **部分失效** —— 藍隊有 `player_id` 與「我這段」狀態，但**不轉個人分數**（§6.5.1） |
| [product-ui spec Q5](../product-ui/spec.md) | 「Blue 的 Portal 很薄，要不要不做」 | **有內容了** —— 兩個嵌入 shell ＋ 自己那段的狀態 |
| SA §12.3 | 「Blue 不取得 Z-MGMT 底層存取權」 | **仍然成立** —— Z-BLUE 不是 Z-MGMT |
| P2 Action Registry（隊級） | 維持隊級 | **維持不變** —— 藍隊偵測能力仍是全場級指標 |
| SA §12.2 四條契約 | 四條 | **五條**（§5.2），且部署新增 kernel 前提（§6.7） |
| [WS2](../ws2-scenario-target/spec.md) objective 來源 | 未區分 | 個人計分 objective **只長在 Z-BLUE**（§6.6） |

## 6.5.1 Blue 有 `player_id`，但不轉個人分數

先把兩件常被混為一談的事拆開：**「藍隊有 `player_id`」與「藍隊有個人分數」不是同一件事。**
前者是座位綁定的必然結果（每人一段機器，不綁人就不知道 shell 該接到哪台），
不是選項；後者才是 #64 要拍的。

**定案：藍隊有 `player_id`、Portal 顯示「我這段」的狀態，但不轉成個人分數。**

**為什麼**：紅藍的分數有一個結構性不對稱 ——

| | 紅隊個人分 | 藍隊「守住」分 |
|---|---|---|
| 分數反映誰的行為 | **自己做了什麼**（拿到 flag） | **對手沒做到什麼**（沒被拿走） |
| 完全不動作時 | 0 分 | **自動滿分** |

紅隊打哪一段是自由的（WS1 無指派目標）。50+ 規模下攻擊分布必然不均 ——
**沒被打的藍隊員白拿滿分，被三個人圍毆的拿 0**。這不是「比較難平衡」，是分數與能力無關。

藍隊真正屬於自己行為的部分（investigation、判讀、封鎖建議），
[WS3](../ws3-blue-ops/spec.md) 與 P2 Action Registry 已定為**隊級**，本決策不影響那條。

**已知代價**：藍隊玩家在 Battleboard 上沒有個人存在感，體驗會比紅隊薄。
若日後要補，方向是「只計主動動作、不計守住與否」，而不是把守住轉成分數。

## 6.6 紅隊主線攻擊面是 Z-BLUE

兩個攻擊面同時存在時，scenario 必須指定主線，否則同一個 objective 會有兩個可能的判定來源。

**定案：主線 = Z-BLUE。個人計分的 objective 只長在 Z-BLUE 上；Z-TARGET 降為保底攻擊面
與 P2 的 coverage／MTTD 分母，其 objective 不進個人計分。**

兩條理由：

1. **§6.5.1 的「我這段守住了沒」只有在紅隊真的會打 Z-BLUE 時才有內容。**
   主線若留在共用的 Z-TARGET，藍隊那段永遠沒人打，狀態恆綠，藍隊的存在變成裝飾。
2. **共用一台 Z-TARGET 在 6 人規模成立，在 50+ 結構上壞掉。**
   第一個人 exploit 成功後改了機器狀態（或把 MySQL 打掛），後面 49 人面對的不是同一張考卷。
   個人計分要求每個人的攻擊面等價，共用靶給不了。

**代價（明確接受）**：P2 的 coverage／MTTD 從此量的是「沒有人打的那台靶」上的偵測能力，
而真正的攻擊發生在 Z-BLUE —— **P2 指標與主戰場脫鉤**。
不把分母搬到 Z-BLUE，是為了保住 §6.3 那條乾淨基準線。§6.7 把 Z-BLUE 升為結構性可信之後，
搬分母的技術障礙已經消失，剩下的只有「基準線要不要保留一塊無人操作的區域」這個取捨——
留給 P2 日後自行決定，本檔不預先決定。

這題屬 WS2，需回寫 scenario schema 的 objective 來源欄位。

## 6.7 Z-BLUE 走容器，collector 在 host 層，部署前提 kernel ≤ 6.8

三條既有事實接起來會撞牆：

1. 50+ 規模 → 藍隊 20 人 × 2 台 = 40 台。**VM 不可行，只能是容器**（§6.4 已假設如此）
2. 契約 4 說 collector 裝在 target 側。但容器**不能**每個都跑自己的 Falco ——
   需要 host kernel 存取＋privileged，且玩家有 shell 就碰得到
3. 所以只能 **host 層單一 Falco 觀察所有 seat 容器**

**定案：Z-BLUE 走容器 ＋ host 層單一 Falco；部署環境的 host kernel 必須 ≤ 6.8。**

kernel 前提的由來：[README](../../README.md) 記載大主機 kernel `7.0.0-28` 上
Falco 0.39.2／0.44.1 的 modern-eBPF 與 kmod 驅動**全部起不來**（CO-RE relocation 對不上），
那正是當初改走 golden VM 的原因。kernel 7.0 是該機的個案，其餘 server 本來就是 6.8。
因此這不是「降版」，而是**寫進部署需求的環境前提**。

連帶效果（正向）：

- Z-BLUE 從「約定式可信」升級為**結構性可信** —— 玩家在容器內碰不到 host 上的 collector
- §6.2 的 sudo allowlist 約束保留，但降級為第二層防線
- golden VM 那條 workaround 有了退場路徑（Z-TARGET 仍沿用，不在本次動）

**契約 4 的語意需要回寫**：「collector 裝在 target 側」在 Z-TARGET 上仍然字面成立，
在 Z-BLUE 上要改述為「collector 與被觀測對象同一台實體主機，且被觀測者無法觸及」。
這屬跨世代契約的措辭變更，需在 SA §12.2 一併處理。

---

# 7. 與既有 workstream 的接縫

| WS | 接縫 | 方向 |
|---|---|---|
| **WS5** Range Core | Admission Service 住 Z-APP；領號成功後鑄造 `player_id` 交給 Range Core | WS8 → WS5 |
| **WS6** Range Infra | seat 表契約（`requested`／`ready`／`released`）；provisioner agent 屬 WS6 | WS8 → WS6 |
| **WS7** Product UI | Player Portal 嵌入 shell（回答 [product-ui Q4](../product-ui/spec.md) 的技術選型與隔離），藍隊為**兩個** terminal 分頁；Instructor Console 需新增 §7.1 的兩個座位操作；**instructor 自身的認證屬 [#52](https://github.com/Graylee0128/cyber/issues/52) 服務身分** | WS8 ↔ WS7 |
| **WS4** Purple | 不接。中控不產生也不消費 Core Event | 無 |
| **WS3** Blue Ops | 藍隊動作現在有落地的機器可執行（每人 a／b 兩台） | WS8 → WS3 |
| **WS1** Game Design | §1.3「Blue 不個人化」需回改為 §6.5.1 的版本 | WS8 → WS1 |
| **WS2** Scenario | objective 來源須指定主線 = Z-BLUE（§6.6） | WS8 → WS2 |

## 7.1 instructor 的座位操作拆成兩個，不是一個

§2.2 把「改綁」定為現場路徑的必要補償。但有一件事必須先講清楚：
**座位綁著 `player_id`，`player_id` 綁著分數 —— 所以動座位就是動分數。**

同一個按鈕會被用在兩種後果完全相反的情況：

| 現場情況 | 應該發生什麼 |
|---|---|
| 玩家換了筆電，要回自己的座位 | `player_id` **不變**，分數延續 |
| 玩家走了，換另一個人接手這台機器 | `player_id` **作廢**，分數歸零重來 |

做成一個按鈕的話，instructor 在現場壓力下按下去，系統只能猜其中一種 ——
猜錯就是把別人的分數送給接手的人，或把玩家累積的分數清掉。

**定案：拆成兩個操作，UI 上明確分開。**

| 操作 | 效果 | 用於 |
|---|---|---|
| **重新綁定 session** | 保留 `player_id` 與分數，只換 session token | 同一個人換裝置 |
| **釋放座位** | `player_id` 作廢、seat 回 `free`（藍池則需手動重建，§4.3） | 換人 |

兩個操作都**必須寫稽核紀錄**（誰執行、何時、哪個 seat、哪種操作）。
這與 [#55](https://github.com/Graylee0128/cyber/issues/55) 提到的
「Instructor Console 的 Override Score／Inject Event 沒有操作紀錄」是同一類問題，
應一起設計成單一稽核管道，不要各自補。

**instructor 自己怎麼認證不在本檔決定** —— 它屬 [#52](https://github.com/Graylee0128/cyber/issues/52)
（WS7 邊界層／服務身分，clearance 不能自報）。但本檔對它有硬依賴：
上述兩個操作的權限強度遠高於玩家領號，**不得與玩家憑據共用同一套驗證路徑**。

---

# 8. 對 SA 的影響（要回寫的）

| SA 章節 | 現況 | 需要改成 |
|---|---|---|
| §4.2 | 「WS4 是唯一跨區的 workstream」 | 不再成立 —— WS8 跨 `EDGE → APP` |
| §12.2 | 四條跨世代契約 | 五條（新增 `EDGE → MGMT` deny all） |
| §12.1 | 世代表 G0／G1／G2／G3(k8s) | 新增 G3＝六區＋中控；k8s 順延為 G4 |
| §12.4 | 「VLAN 數量（G2 定為四區）已不再是未決事項」 | 重新開啟 —— 四區 → 六區 |
| §4 | 七個 workstream | 八個（新增 WS8 Event Control Plane） |
| §12.2 契約 4 | 「collector 裝在 target 側」 | 措辭需擴充 —— Z-BLUE 的 collector 在 host 層（§6.7） |
| 部署需求 | 未規定 host kernel | **新增前提：host kernel ≤ 6.8**（Falco 驅動支援範圍，§6.7） |
| §9 Objective | 未區分攻擊面 | 個人計分 objective 只長在 Z-BLUE（§6.6） |

---

# 9. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼不在本檔決定 |
|---|---|
| ttyd 的具體前端（xterm.js 版本、字型、複製貼上行為） | 實作層細節，不影響任何 schema 或跨區政策 |
| **玩家**操作側錄的格式 | 與 [product-ui Q3](../product-ui/spec.md) 是同一類問題，應一起決定，不在本檔單獨定。註：**instructor 的座位操作稽核已定案為必要**（§7.1），但格式同樣留給那次一起定 |
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
| 15 | 座位規模 | 50+ | §4 |

## 10.1 第二輪（#65 decision gate，2026-08-12，10 題）

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 16 | 誰決定紅／藍隊 | **玩家自選**；憑據不帶隊伍資訊 | §2.1、§3.2.1 |
| 17 | 池上限與額滿 UI | instructor 開場前設；伺服器端檢查；額滿**反灰**不可選 | §3.2.1 |
| 18 | Blue 個人化 | 有 `player_id`、有「我這段」狀態，**不轉個人分數** | §6.5.1 |
| 19 | 紅隊主線攻擊面 | **Z-BLUE**；Z-TARGET 降為保底靶＋P2 分母 | §6.6 |
| 20 | Z-BLUE runtime 與遙測 | 容器 ＋ **host 層單一 Falco**；部署前提 kernel ≤ 6.8 | §6.7 |
| 21 | 50+ 承載 | **先做承載 spike，再開 #62**（真 kali image、目標主機上跑） | §11 |
| 22 | 藍隊 shell | 一段兩台**各一個 ttyd**，兩個分頁；不走跳板 | §5.4 |
| 23 | `player_id` 鑄造點 | `requested` 鑄造、`ready` 才交 Range Core；逾時＋重試＋告警 | §4.4 |
| 24 | instructor 座位操作 | 拆成**重綁 session／釋放座位**；兩者都寫稽核 | §7.1 |
| 25 | 藍池建置與鎖點 | **start 時全建**、依實到人數；start 即鎖池；紅池仍動態且中途可領 | §4.3 |

---

# 11. 待決（需另行拍板，本檔不預設）

§11 原本的三題已於 #65 全數拍板（決策 19／20／21），改列為下列一件**必須先做的實測**
與兩件外溢到其他 workstream 的回寫。

## 11.1 承載 spike —— #62 開工前的前置

單主機要扛的：紅隊 seat 容器（kali-rolling）、藍隊 seat 容器（每人 a＋b 兩台，
且**開場即全數啟動**，§4.3）、每 seat 至少一個 ttyd、Z-TARGET golden VM、觀測棧一套。

要量的四個數：

1. RAM 峰值
2. OVS port 上限
3. **容器啟動到 `ready` 的時間** —— 這是 §4.4 逾時值 T 的來源
4. 磁碟（N × kali layer）

執行前提：

- 跑在**符合 kernel ≤ 6.8 前提的目標主機**上，不是開發機
- **必須用真的 `kalilinux/kali-rolling`**，不能用 `attach-red.sh` 預設的 `nicolaka/netshoot` ——
  netshoot 是幾十 MB 的除錯工具箱，用它測出來的數字會讓人誤判撐得住
- 基準情境是「藍隊池全開 ＋ 紅隊陸續進場」，不是 70 個容器的穩態

現有腳本已足夠：`attach-red.sh` 的 `COUNT` 與 `RED_IMAGE` 都是覆寫點。

撐不住的話會逼出多主機，那會動到 OVS trunk 設計 —— 這正是要先測的原因。

## 11.2 外溢的回寫（不在本檔完成，需各自拍板）

| 項目 | 歸屬 |
|---|---|
| 契約 4 措辭擴充（host 層 collector）＋ SA 部署需求新增 kernel 前提 | SA §12.2、#20 |
| scenario objective 來源指定主線 = Z-BLUE | WS2 |
| instructor 認證與稽核管道（與 Override Score／Inject Event 合併設計） | [#52](https://github.com/Graylee0128/cyber/issues/52)、[#55](https://github.com/Graylee0128/cyber/issues/55) |
