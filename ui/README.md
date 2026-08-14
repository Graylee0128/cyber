# Product UI

平台的六個對人畫面。零依賴靜態頁（無 build step、無 npm），由 nginx 服務並代理後端。

| 畫面 | 目錄 | Audience | gateway 身分 |
|---|---|---|---|
| Battleboard | `battleboard/` | 全場、教室大螢幕 | `red`（clearance 0） |
| Player Portal — Red | `player/index.html` | Red | `red` |
| Player Portal — Blue | `player/blue.html` | Blue | `blue` |
| Blue SOC Console | `blue-soc/` | Blue | `blue` |
| Purple Console | `purple/` | 分析師、Instructor | `purple` |
| Instructor Console | `instructor/` | Instructor | `instructor` |
| Event Control | `event-control/` | 會議中控 | `instructor` → Admission |

## 技術選型：為什麼是零依賴靜態頁

`purple-console-ui/spec.md` Q4 與 `product-ui/spec.md` 的前端技術選型此前都是空白。定為
**選項 A（零依賴靜態 HTML/CSS/JS）**，理由三條：

1. 六份視覺提案本來就是零依賴單檔，沿用等於不作廢已經跟組員對過的設計
2. 這是一個純 Python repo，加一套 Node toolchain 要同時進 CI、進 image、進真 range 主機，
   而這六個畫面是讀多寫少的表格與清單，不需要為此付那個代價
3. Z-EDGE 已經有 nginx。靜態 bundle 直接掛 volume 就能服務，沒有建置產物要管

改用框架不是不行，但要等到有一個「靜態頁做不到」的具體需求，而不是預先假設。

## 權限模型：token 不進瀏覽器

Range Core、Evidence API、Admission 的呼叫者身分都由**部署時注入的服務 token** 換出
（`disclosure.identity`）。那個保證 —— 呼叫者無法自報身分 —— 只在 token 不在呼叫者手上時
才成立。前端持有 token 等於當場作廢：紅隊玩家打開 devtools 就能拿 instructor token 去
`POST /api/exercises/reset`，把整場演練清掉。

所以：

```
瀏覽器  ──不帶 Authorization──▶  nginx  ──貼上該前綴的 token──▶  後端
        /gw/blue/core/api/score              Bearer <blue token>
```

前綴決定身分，身分決定 clearance，clearance 決定後端遮掉哪些欄位。**遮蔽發生在後端組裝
回應時**，不是前端渲染時 —— 前端遮是假的，devtools 打開就看到。token 表在
[`deploy/ui/default.conf.template`](../deploy/ui/default.conf.template)，那個檔案就是這層邊界本身。

同樣的機制也管 Battleboard 的延遲揭露：`revealed` 參數由前綴強制寫死，公開前綴永遠 `false`。

**推論：一個頁面只能有一個身分。** 紅藍玩家視角因此是兩個檔案（`player/index.html` 與
`player/blue.html`），不是同一頁的兩個分頁 —— 分頁式的做法要讓一頁同時持有 red 與 blue
兩個 gateway，而 red 的 clearance 是 0、blue 是 1，等於給紅隊玩家一個「切一下就升級
可見範圍」的按鈕。切換身分＝換頁。

## 跑起來

```bash
docker compose --profile admission-e2e up -d --build ui evaluation-api admission-range-core admission admission-receiver
```

畫面在 <http://localhost:8090/>。

Blue SOC 的 Grafana 分頁與 Evidence 查詢需要**預設 profile 也起著**（`grafana`、
`evaluation-engine`、`loki` 住在那邊）：

```bash
docker compose up -d grafana evaluation-engine loki
```

注意兩個 profile 各自帶一座 PostgreSQL，狀態不共用 —— 跨 profile 查證據時，
`evaluation-engine` 讀的是預設 profile 那座庫，access plane 產生的 event id 在那裡查不到。
要完整的證據鏈就得讓兩邊指向同一座庫，那屬部署決策，不在本次範圍。

## 已知缺口

這些是**真的沒做**，不是沒寫完。放在這裡是為了它們不會被畫面的完整度掩蓋掉。

### 1. 來源 IP 歸屬與反向代理相衝

`POST /api/submissions` 與 `POST /api/hints` 用 TCP peer address 當名冊的鍵，且**刻意不信任**
`X-Forwarded-For`（`range_core/api.py` 的 `_source_ip`：信了就等於讓一個紅隊玩家能用別人的
身分提交 flag）。經 gateway 進來的請求，Range Core 看到的是 nginx 的位址，所以會回
403 `source IP is not on the exercise roster`。

畫面照實顯示這個錯誤，不假裝成功。真正的解法是部署拓樸的決定（Portal 要從玩家自己的座位
服務出去，或 Range Core 要坐在 Kali 直連得到的位置）—— `_source_ip` 的 docstring 自己也記著
「deployment topology is not yet decided」。

### 2. 誰能載入教官畫面，目前只靠網段擋

`UI_PRIVILEGED_CIDR` 是現在唯一擋得住「拿到網址就進得去 Instructor Console」的東西。
Admission 的 session 只回答「這個 session 擁不擁有這台終端機」（`/admission/auth/ttyd/{terminal}`），
沒有「這個 session 是不是教官」的端點，所以 nginx 的 `auth_request` 接不上去。
compose 裡的預設值是 `0.0.0.0/0`（本機 demo 用），**正式環境必須收斂到 Z-MGMT 網段**。

### 3. `GET /api/scenarios` 會吐出攻擊鏈

那條端點回的是完整 scenario 扣掉 hint 文字 —— `attack_chain`（每一步的 MITRE technique
與描述）仍在回應裡。WS2 spec §4.2 明訂 briefing 不給攻擊鏈，Battleboard 甚至為此把技法
匿名化成 `Attack #N`，但紅隊直接打這條端點就全拿到了。

Player Portal 不渲染它，可是前端不渲染不等於沒外洩。這要在後端補投影，屬 WS5／#82 的範圍。

### 4. 逐來源的 Telemetry 欄沒有出口

`purple/console/drilldown.py` 的 ✅／❌／—／⏳ 判定邏輯是完整的，但 Evaluation API 只吐引擎
算好的缺口分類，不吐每個來源的個別標記。Purple Console 畫面二因此顯示缺口分類（那正是
Telemetry 欄要回答的問題），並在畫面上標明少了哪一層，而不是留白。

### 5. briefing 沒有 API

`scenarios/<id>/briefing.md` 是檔案，沒有任何 HTTP 出口。Player Portal 的 Mission 面板
目前只顯示 scenario 的中繼資料（名稱、難度、時長、目標主機）。

## 這次一併補的後端出口

三個「判定邏輯早就寫好、但部署裡沒有出口」的縫：

- `GET /api/techniques` —— technique 判讀限制（#26 的 acceptance criteria 要求常駐顯示，
  而那段文字只存在於 `config/techniques.yaml`）
- `GET /api/exercises/{id}/battleboard` —— `purple.battleboard.sanitize`（#82）此前沒有
  任何呼叫者
- compose 的 `evaluation-api` service —— **Evaluation API 從來沒有被部署過**。既有的
  `evaluation-engine` 名字像它，跑的其實是 `purple.evidence.service`

第三項與 #51 那兩個環境變數是同一類問題：單元測試全綠，功能在真部署裡不存在。
[`tests/access_integration/test_ui_gateway.py`](../tests/access_integration/test_ui_gateway.py)
就是為了讓這類縫會變紅而存在。
