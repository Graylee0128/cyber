# Spec — WS2 Scenario / Target

- **Status**: draft（定案後比照 P1 遷出至 `docs/`；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-11 grilling session（十七題逐一確認，本檔為落地記錄）
- **上位文件**：[SA](../../資安攻防平台_系統架構設計文件_v0.1.md) §4、§7、§11、§12；[purple_platform_plan.md](../../purple_platform_plan.md) §3.2–§3.5
- **上游依賴**：[WS1 遊戲規則 spec](../ws1-game-design/spec.md)、[WS5 Range Core spec](../ws5-range-core/spec.md)
- **既有實作**：`src/range_core/scenarios.py` 與 `scenarios/sqli-01.yaml`（[#31](https://github.com/Graylee0128/cyber/issues/31) / PR #40，2026-08-11 合併）

WS2 是 SA §4.1 的內容線，負責靶機、漏洞、攻擊鏈、Flag 與 Scenario Package。

**本檔不是 greenfield 規格，是遷移規格。** `#31` 已在 2026-08-11 合併，scenario schema 與載入器已經存在
且有測試。本輪 grilling 的結論與已交付的實作有六處衝突（§7），必須以遷移票處理，不能當作新建。

---

# 0. 兩條貫穿全局的原則

這兩條在 grilling 過程中浮現，回頭驗證每一題的答案都落在它們的正確一側。日後新欄位歸屬有爭議時，先問這兩條。

## 0.1 擁有 vs 引用

> **紅方的東西 scenario 擁有**：攻擊面需求、攻擊鏈、flag、objective、hint、briefing。
> **藍方與平台的東西 scenario 只引用並驗證**：偵測規則、遙測來源定義、technique 白名單。

「引用並驗證」的意思是 scenario 檔寫下它預期的名字，載入時檢查該名字存在，**但不攜帶本體**。
違反這條的具體後果見 §2.2（偵測規則若由 scenario 攜帶，coverage 會永遠漂亮）。

## 0.2 預付的判準

> **在會被人手寫的地方預付，在有測試接住的地方不預付。**

scenario 檔是內容作者手寫的，改格式要改掉每一份已寫好的檔案，而那些檔案沒有測試接得住 ——
所以 `targets` 從第一天就寫成清單（§3.2）。反之 `zones.env` 的 `TARGET_IP` 有
`tests/range/test_zones_env.py` 機器檢查，改壞會紅燈，所以不預付（§3.3）。

---

# 1. Scenario Package 的形態與歸屬

## 1.1 一份檔，WS5 是載入器、WS2 是內容作者

Scenario Package 就是 `#31` 已經定義的那份檔，**不新增第二種格式**。WS2 的工作是補內容欄位並
收斂目前分散的 scenario 身分，不是另造格式。

**為什麼**：scenario 身分現在活在三處 —— `config/scenario-sources.yaml` 的 `scenarios:` 區塊、
`scenarios/*.yaml`、Grafana `rules.yaml` 的 `scenario_id` label。已經是三塊碎片；切成
「WS5 管 game 欄位、WS2 管 content 欄位」的兩份檔只會變成四塊，而在只有一套 range、
一個 scenario 的規模下買不到任何隔離價值。

## 1.2 落點：`scenarios/<id>/` 目錄，不是單檔

```text
scenarios/<id>/
├─ metadata.yaml     ← 所有機器讀的宣告
└─ briefing.md       ← 給玩家看的任務說明
```

**為什麼是目錄而非單檔**（依 §0.2 裁決）：briefing 與 hint 是散文 —— 中文、有排版、可能好幾段。
塞進 YAML 多行字串是已知的痛；日後要拆出來就得改 loader **並改掉每一份已寫好的 scenario**。
反方向的成本是零 —— 「目錄裡兩個檔」不構成負擔。不對稱，所以現在就付。

**為什麼落在 repo 根層 `scenarios/` 而非 `config/`**：`config/` 住的是**全域一份**的平台設定
（時鐘節點、technique 白名單、遙測來源定義）。scenario 是**內容庫**，會長大 ——
SA §16 V3 的 Scenario Marketplace 就是它長大的形態。兩種生命週期不該住在一起。

這也讓 §2.1 那個「bounded context 在檔案層被穿透一次」的代價比較好看：Z-MGMT 的程式讀的是
`scenarios/` 這個明確的內容庫，不是伸手進另一個 context 的內部設定。

## 1.3 SA §11 的六目錄草案已塌成一份檔

| SA §11 草案 | 本輪之後的歸屬 |
|---|---|
| `metadata.yaml` | **唯一活下來的**，並吸收了下面全部 |
| `detection/` | 不擁有，只引用規則名稱＋明示缺口（§2.2）|
| `target/` | 不擁有，攻擊面烤在共用 golden，scenario 只宣告需求（§3.1）|
| `reset/` | 塌成一個欄位 `reset_scope`（§5.2）|
| `scoring/` | 塌成 objective 的 `points` —— 分數是推導的，沒有規則檔（[WS5 spec §1.3](../ws5-range-core/spec.md)）|
| `hints/` | `#31` 已把 hint 放進 scenario 定義本體 |

SA §11 應標註為已被本檔取代。空目錄比沒有目錄更糟 —— 它會誤導後來的人以為那裡該放東西。

---

# 2. 與平台的交界（只引用，不擁有）

## 2.1 `expected_sources` 搬進 scenario 檔

`config/scenario-sources.yaml` 目前混了兩種性質不同的東西，必須拆開：

| 區塊 | 內容 | 歸屬 |
|---|---|---|
| `sources:` | 每個 collector 的 heartbeat 怎麼從 Loki 讀 | **平台級**，留在 `config/` |
| `scenarios:` | 每個 scenario 的 `expected_sources` | **內容級**，搬進 `scenarios/<id>/metadata.yaml` |

**為什麼搬**：[WS1 spec §4](../ws1-game-design/spec.md) 已明講「哪些來源 `expected` 由 scenario
作者決定 —— hard scenario 就少放幾個」。那跟「這個 scenario 想讓藍隊在什麼能見度下作戰」是**同一個決策**，
拆兩處等於把一個決策切成兩半。

**P2 怎麼拿到**：讀同一份 repo 內的宣告檔，**不是執行期呼叫 Z-APP 的 API**。

**為什麼不走 API**（雖然語意更乾淨）：那會讓 Z-MGMT 的判讀能力依賴 Z-APP 的服務可用性。
`#20`（APP↔MGMT 四條流量規則）尚未實作，Range Core 也才剛有第一支程式 ——
拿一個已在真環境跑通的東西去換一個還不存在的東西，方向是反的。

**已知代價**：bounded context 的邊界在檔案層被穿透一次（`src/range_core/` 刻意不 import `purple`，
但 `purple` 側會讀 `scenarios/`）。可接受的理由是它是 **deploy-time artifact 而非執行期耦合**。
若日後 Range Core 成熟且 `#20` 落地，改走 API 是換讀取來源，不是換架構。

## 2.2 偵測規則只引用，且 `detection:` 不是分母

scenario 檔列出它預期的 Grafana rule 名稱，**並明示哪些 technique 是刻意無覆蓋的**。
規則本體留在 `deploy/grafana/provisioning/alerting/rules.yaml`，由紫隊維護。
載入 scenario 時驗證引用的規則存在 —— 缺了就**開演前失敗**，而不是紅隊打了二十分鐘才發現沒告警。

**為什麼 scenario 不攜帶規則本體**：一旦攜帶，作者寫 scenario 時會自然把自己想得到的攻擊都配好規則，
coverage 永遠漂亮。這正是 [plan §1.1](../../purple_platform_plan.md) 要把分母定義權留在紫隊的原因。
工程上也有代價 —— per-scenario 規則要合併就得加 build 步驟並在每場演練前重新 provision，
而 ADR ③ 已定「Grafana Alerting 是唯一 alert engine」，規則本質上是全域的，只靠 label 分場。

**必須寫死的但書**：`detection:` **不是 coverage 的分母**。分母永遠是 `#21` Action Registry。
這個欄位只做兩件事 —— 開演前的存在性驗證、以及明示刻意缺口。
若不寫死，`#22` 算 hit/miss 時會出現第二套分母，與 WS1 spec §2.1 拒絕的是同一種病。

**明示缺口為什麼是進步**：`falco-uncovered-01` 現在的缺口是隱性的 —— 靠沒人手賤去補規則來維持。
讓 scenario 明講「這個 technique 我刻意不給覆蓋」，缺口才從口耳相傳變成可驗證的宣告。

## 2.3 攻擊鏈是 Action Registry 的來源，不是它的替代品

scenario 檔攜帶攻擊鏈（動作清單：id、ATT&CK technique、描述）。
**開演時載入成 `#21` Action Registry 的初始內容，開演即凍結。**

```text
scenarios/<id>/metadata.yaml   →   開演前載入   →   Postgres（可調整）   →   開演凍結
        （來源）                                                          （實例）
```

**這與 `#21` 拒絕過的選項不衝突**：`#21` 在「清單存哪裡」拒絕了「檔案（scenario package 的一部分）」，
理由是「開演後凍結變成靠約定而非機制」。它拒絕的是**用檔案當凍結機制**，不是用檔案當來源。
本決策完整保留 Postgres ＋ 交易凍結，只把「開演前那份清單從哪來」從人腦搬到檔案。

**為什麼不讓教官每場手打**：同一個 scenario 的不同場次分母會漂，打漏一個就是涵蓋率假性上升 ——
那正是 `#21` 存在要解決的問題。用手打來解決「怕人漏報」，是拿病因當解藥。

**這不違反「分母定義權留紫隊」**：WS1 spec §2.1 拒絕的是讓 **Objective（敘事層，「Capture Flag」）**
定義分母；攻擊鏈是**技術層**（T1190 → T1059 → T1005），與 Action Registry 本來就是同一種東西。
紫隊的控制權透過兩個既有機制行使：`config/techniques.yaml` 白名單驗證（`#21` 的 AC 已有）＋凍結機制。

**檔案是預設值，不是死值**：開演前可調整，開演即凍結。教官要為這場加一個動作，改完再開演即可。

---

# 3. 靶機與攻擊面

## 3.1 攻擊面二分：可計分的要真，fixture 維持模擬

| 類別 | 要求 | 例子 |
|---|---|---|
| **可計分攻擊面** | **必須真的可利用** —— 紅隊得真的打進去 | 第一個 scenario 的 web 漏洞鏈 |
| **測試載具（fixture）** | 維持模擬、明確標記、**不掛 objective、不屬於任何 scenario** | `/exec`、`/readsecret`、`/uncovered`、`/healthz`、alloy heartbeat |

**為什麼可計分的必須真**：現況 `/exec` 是 GET 一下就生 shell、`/readsecret` 是 GET 一下就吐 secret。
在這種靶機上，`trigger_sqli` 這種「遙測自動判定」的 objective 變成 curl 一下滿分，
`capture_flag` 要玩家提交的那個 flag 一個 curl 就拿到。**WS1 已拍板的八個決策全部預設 objective 有難度** ——
Hint 扣分、Red 分個人、混合判定，在假漏洞上通通變成佈景。

**為什麼 fixture 不能一起變真**：`/uncovered` 存在的唯一理由是「Falco 抓得到但刻意沒有 Grafana 規則覆蓋」，
它是決定性測試的載具，不該有難度、不該有分數。全部要真＝把測試載具也搞複雜。

**二分的附帶好處**：T2／T4 測試繼續打 fixture 端點，**不會因為 WS2 動漏洞而變紅**；
scenario 打真攻擊面。兩者互不干擾。

**代價（必須認）**：真 range VM 目前**可計分攻擊面是零**。建立它必然要**重烤 golden**
（`golden_stamp` 對所有烤入來源檔算 sha256，來源一改就重烤）。這筆錢躲不掉，只能決定何時付。

## 3.2 單一多漏洞 golden，攻擊面全開；scenario 宣告需求

所有 scenario 的攻擊面共用同一顆 golden image，**永遠全開**。scenario 檔宣告它**需要**哪些攻擊面存在，
載入時驗證 —— 形狀與 §2.2 的 detection 完全一致：宣告＋驗證，不擁有本體。

**為什麼不是每 scenario 一顆 image**：每加一個 scenario 多一次 golden bake，演練之間還要換 VM。
在「同時只有一場演練」（[WS5 spec §2.1](../ws5-range-core/spec.md)）的前提下，多顆 image
買不到任何並行能力，只買到等待時間。

**為什麼不做「開演時啟用」的開關**：現在沒有任何一個 scenario 需要關掉別人的攻擊面。
為此蓋一套機制，是 WS1 spec §4 拒絕過的同一種錯 —— 沒有實例就訂規則，訂的是空氣。

**全開反而更貼近訓練真實**：SA §3 要求紅隊「探索目標」。真實靶機本來就比任務需要的洞更多；
攻擊面等於任務範圍，等於告訴紅隊「這台機器上只有一個洞，就是你要打的那個」，偵察這件事直接消失。

**破局觸發條件**（寫在這裡，屆時不必重新論證）：當出現「同一個洞在 A scenario 要開、
在 B scenario 要已修補」的需求時（WS3 藍隊修補驗證很可能就是），本決策撐不住，
那時再上「開演時啟用」。屆時是增量，不是重工。

### 關卡與依序破解

攻擊面全開**不妨礙**設計依序破解的關卡。三種 gating，只有第三種需要開關機制：

| 機制 | 怎麼擋 | 需要什麼 |
|---|---|---|
| **內容鏈接** | 前一關的產出是後一關的鑰匙（SQLi 撈出憑證 → 憑證才開得了下一道門）| 只需要 §3.1 的真漏洞 |
| 計分層 gating | objective 前置依賴 | 一個 schema 欄位 —— **本檔不做**，見 §4.1 |
| 基礎設施 gating | 服務真的不存在，第二關才開 | 需要 §3.2 的開關機制 |

**真正決定「能不能設關卡」的是 §3.1，不是 §3.2。** 漏洞若是假的（GET 一下就吐 secret），
不管攻擊面全開或分段開放都擋不住 —— 玩家直接跳到最後一關。

## 3.3 v1 單機縱深，不做橫向移動，也不預付多機維度

攻擊鏈是「web 漏洞 → 取得 shell → 本機提權 → 拿到敏感資料」這種**單機縱深**。
多機橫向移動留給 V2。

**加第二台的實際成本**（實查，比直覺低）：

| 項目 | 成本 |
|---|---|
| 網路可達性 | **零**。兩台都在 VLAN20 同一 L2 網段；四區契約只管跨區，同區內今天就通 |
| 靶機 image | **零**。同一顆 golden 開兩台，差別只在 IP 與角色 |
| Core Event `target` | 加 `host` 欄位，JSONB additive |
| `config/clock-nodes.yaml` | 加一列 |
| **`scripts/range/zones.env` 的 `TARGET_IP`** | **唯一真的門** —— 純量變清單，shell 與 Python 的所有消費者跟著動 |

**為什麼不預付**（依 §0.2）：`zones.env` 有 `tests/range/test_zones_env.py` 機器檢查，
改壞會紅燈提醒。這與 WS5 對 `exercise_id` 的預付不同 —— 那個已經在每張表、每個查詢裡，拔掉是 wide refactor。

**唯一要預付的**：`scenarios/<id>/metadata.yaml` 的 target 宣告**從第一天就寫成以 host 為單位的清單**，
即使 v1 永遠只有一台。理由見 §0.2 —— 這是人手寫的檔案。

```yaml
targets:
  - host: target-01        # v1 唯一一台，對應 zones.env 的 TARGET_IP
    surfaces: [...]        # 這個 scenario 需要存在的攻擊面
```

**破局觸發條件**：出現需要橫向移動或多主機縱深的 scenario 時，開一張 WS6 票把 `TARGET_IP` 純量改成清單。

---

# 4. 遊戲規則的落地

## 4.1 Objective 扁平，不加前置依賴

**不設 `requires` 欄位。** 順序完全交由 §3.2 的內容鏈接自然形成。

**為什麼**：

1. **內容鏈接已經是真的擋住了，前置依賴是重複一道假的。** 若玩家在沒有前置的情況下真的完成了後續
   objective（他找到出題者沒想到的路徑），那是**紅隊贏了**，不是作弊。此時判他不計分，
   等於懲罰比出題者強的人。
2. **`requires` 會被誤用成內容設計的替代品。** 有了它，作者可以偷懶把 flag 隨便放，反正欄位會擋 ——
   這正是 §3.1 要避免的事。安全網會讓爛內容看起來像好內容。
3. Battleboard 要畫攻擊鏈進度，用**已完成 objective 的順序**就畫得出來，不需要預先宣告的 DAG。
   真需要時再加是純增量。

## 4.2 紅隊自由發揮，不照劇本

briefing 只給任務目標，路徑自己找。攻擊鏈（§2.3）是**預期路徑**，用來當分母，不是給紅隊照著執行的清單。

**為什麼不是照劇本**：照劇本與 WS1 已拍板的八個決策直接矛盾 ——

| WS1 決策 | 照劇本之後 |
|---|---|
| Hint 扣分（§3）| 劇本已寫了答案，hint 沒有存在意義 |
| Red 分個人（§1.3）| 同一份劇本，六個人分數應該一樣，沒有鑑別對象 |
| 混合判定、flag 要提交（§2.2）| 提交變成走過場 |
| README「Gamified…可計分」| 照劇本的紅隊不是玩家，是壓力產生器 |

「兩種模式由演練設定決定」更糟：兩種模式產出的 coverage 意義不同，
再次撞上 [plan §3.2](../../purple_platform_plan.md)「所有 scenario 一致，可比」。

## 4.3 自由發揮開出的洞：註冊但未執行

[plan §3.2](../../purple_platform_plan.md) 的模型是 `executed actions = hit(C1+) + miss + unknown`，
分母是**執行過的**動作。陷阱①自己寫了「上報了沒成功執行的動作，涵蓋率假性下降」，
但預先註冊只解決了漏報，**沒解決「註冊了但紅隊沒去執行」**。

而 plan §3.4 說沒有 log 的漏檢叫**可見性缺口**，該補收集來源。於是：

> **紅隊沒執行的註冊動作 → 沒有任何 log → 被判成藍隊的可見性缺口。**

這正是 §3.4 開宗明義要避免的「冤枉藍隊」。**這個洞只有在紅隊可以不照劇本走時才會裂開**，
所以 §4.2 選了自由發揮，就必須連帶付這筆錢。

**兩項要求**：

1. 分母裡的每個動作必須能**獨立驗證「有沒有被執行」**，且該證據**不得來自偵測規則** ——
   否則是循環論證（用「偵測到了」證明「執行了」，再用「執行了」當分母算偵測率）。
   現成材料：靶機 app 對每個請求都寫一行帶 `source_ip` 的 JSON log
   （`deploy/range-target/app.py`），其存在與 Grafana 有沒有規則無關。
2. **P2 需要第四種狀態。** 目前是 `hit` / `miss` / `unknown`；**`not_executed`（註冊但未執行）**
   必須從 `miss` 分出來，**不進分母、也不算可見性缺口**。

### `not_executed` 與 `unknown` 必須分開，不得合併成「其他」

兩者都不進分母，但**改善動作完全相反**：

| 狀態 | 意義 | 該做什麼 |
|---|---|---|
| `unknown` | 來源掉線或資料不足，**無法判定** | 修遙測 |
| `not_executed` | 紅隊**根本沒做這個動作** | 檢討劇本或紅隊執行 |

合併呈現會讓教練無從判斷該修哪一邊。

### 這條漣漪流到四張 P2 的票，不是一張

第 2 項不是 WS2 的票，但它**不只影響 `#22`**。第一次落地時只改了 `#22`，
2026-08-11 的票體檢掃出另外三張也接不住 —— 記在這裡以免再漏：

| 票 | 漏在哪 | 已補 |
|---|---|---|
| `#22` P2-2 動作判定 | 第四狀態的判定本身 | ✅ |
| `#24` P2-4 三個核心數字 | 公式仍是 `executed = hit + miss + unknown`，未排除 `not_executed` | ✅ |
| `#26` P2-6 Coverage 表 | 只有 ✅／❌／⏳ 三態，`not_executed` 無符號 | ✅ |
| `#28` P2-8 Exercise Report | 無 `not_executed` 數量 → 分母縮小而讀者無從得知 | ✅ |

**教訓（比這條決策本身更值得記）**：spec 改一條，下游票沒有人負責回頭掃，
就會長出 PR #40 那種「照著已作廢規格寫程式」的情況。**改 spec 之後掃票是流程的一部分，不是可選的收尾。**

---

# 5. Flag 與 Reset

## 5.1 Flag 每次 `range-up` 輪換，開機注入

flag 值**不烤進 golden**，由 `range-up` 產生後經 cloud-init 在開機時注入。

**為什麼不能靜態**：訓練產品的 flag 一旦固定，上過課的人把答案傳給下一梯，
`capture_flag` 這個 WS1 §2.2 唯一走「玩家提交」的 objective 就永久失效。

**為什麼不烤進 golden**：`golden_stamp` 讓「換 flag」＝「重烤 image」，分鐘級操作換一個字串。

**為什麼不是每場演練輪換**（語意上更好的那個選項）：flag 的權威是 Range Core（Z-APP），
要讓靶機 pull 就需要 `TARGET → APP` 通道 —— `#20` 有這條規則但尚未實作，而 Range Core 才剛起步。
為一個還沒長好的服務先蓋 pull 通道，等於把 WS2 的進度綁在產品線上不上線。
cloud-init 注入則是 `build-vm-target.sh` **已經在做的事**（探測腳本就走 `write_files` ＋ base64）。

**已知代價**：flag 輪換綁在**環境層**，不是演練層。同一套環境連跑三場，三場同一個 flag。
操作規則是一句話：**換班就重跑 `range-up`**。

**升級觸發條件**：出現「同一套環境連跑多場、每場要不同 flag」的需求時（同一天多組輪流上場），
升級成靶機側 pull。屆時是**換注入時機**，scenario 檔與比對邏輯都不用動。

**Range Core 怎麼知道當場的 flag**：`range-up` 產生後寫到一個雙方都讀得到的檔案 ——
與 §2.1 同一種手法（宣告檔共享，不建執行期跨區呼叫）。

## 5.2 Reset 維持兩層，scenario 宣告自己需要哪一層

[WS5 spec §2.3](../ws5-range-core/spec.md) 的兩層不變（演練狀態毫秒級／環境分鐘級）。
**不加第三層靶機 reset。** 改為在 scenario 檔宣告：

```yaml
reset_scope: exercise | environment
```

**為什麼不加第三層**：libvirt snapshot revert 確實快，但它是 WS6 的工作，
而 WS5 spec §2.3 剛用三條理由論證過「兩層不該焊死」。現在加第三層，是在還沒有任何一個 scenario
證明需要它的時候先蓋機制 —— 與 §3.2 拒絕開關機制是同一個判斷。

**為什麼靶機不能設計成無狀態**：與 §3.1 矛盾。真漏洞必然留狀態 —— SQLi 改得動 DB 才叫 SQLi。

**環境 reset 的實際成本比聽起來低**：`build-vm-target.sh` 是在 golden 上開 qcow2 overlay，
不是重烤；`range-reset.sh` 也刻意不刪 golden。所以是分鐘級，不是十幾分鐘級。

**為什麼需要那個宣告**：不是每個 scenario 都髒。純暴力破解不留靶機狀態，用毫秒級的演練 reset 就夠；
落了檔案的 RCE scenario 非得重建環境不可。**沒有這個宣告，操作者只能一律用最貴的那層**，
WS5 那個毫秒級 reset 實質上就廢了。有了它，「這場能不能快速重跑」變成 scenario 作者要回答的問題。

**升級觸發條件**：現場出現「同一批人連跑五場、每次都等重建」的痛點時，加靶機層 reset。
屆時 `reset_scope` 已經在了，升級只是多一個合法值。

---

# 6. 環境與範圍

## 6.1 只有真 range 算 scenario

compose 的 `vulnerable-app` 降格為**純管線測試載具**，不再擁有 scenario id。

**為什麼**：

1. **可比性。** plan §3.2 要求 MTTD 等指標「所有 scenario 一致，可比」，WS1 §5.1 為了同一個
   可比性把演練訂成純時間制。但 compose 與真 range 的**遙測拓樸根本不同** ——
   一個是 app 自己寫 log、一個是 VM 內 Falco 看 syscall。承認兩種環境等於承認兩種 MTTD。
2. **compose 裡沒有四區 VLAN、沒有六台 kali 各自的 source IP。**
   WS1 spec §1.3 的「Red 分個人」在 compose 裡無法成立 —— 沒有可分辨的來源就沒有歸屬。
   一個算不出個人分的環境不該叫 scenario。
3. 合併兩支 app 買錯東西：它們的差異不在攻擊面，而在**偵測機制**。
   合併程式不會讓 compose 長出 VLAN 隔離。

**已知代價**：scenario 這一層**永遠不在 CI**（T3／T4 本來就不在），只能在有巢狀虛擬化的機器上驗。

## 6.2 現有五個 scenario id 全部不是 scenario

| id | 判出局的理由 |
|---|---|
| `sqli-01` | compose only，真 VM 上沒有 SQLi（§6.1）|
| `bruteforce-01` | compose only，真 VM 上沒有登入（§6.1）|
| `falco-exec-01` | `/exec` 是 GET 一下就生 shell → fixture（§3.1）|
| `falco-secret-03` | `/readsecret` 是 GET 一下就吐 secret → fixture（§3.1）|
| `falco-uncovered-01` | 存在的唯一目的就是當測試載具 → fixture，而且是刻意的 |

**WS2 的真實起點是零個 scenario，不是五個。**

**處置方式：字串不動，改的是它們住在哪。**

- `config/scenario-sources.yaml` 的 `scenarios:` 區塊**清空**（內容依 §2.1 搬進 scenario 檔）
- 五個 fixture 的 `expected_sources` 移到新的 `fixtures:` 區塊
- loader（`src/purple/registry/production.py`）兩個區塊都讀，但語意分開

**為什麼不全面改名**：那五個字串散在 Grafana `rules.yaml` 四條規則的 label、
**已定版**的 `docs/p1-output-contract.md`、以及 **12 個測試檔** —— 其中
`test_falco_range_chain.py` / `test_falco_pipeline.py` 屬 T3／T4，**不在 CI**。
改名的收益是字串好看，風險是動到唯一綠著的那組證據，而且改壞了**不會在 CI 紅，
會在下次上大主機時才紅、且一次紅一片**。

**為什麼不是「完全不動、只在文件註明」**：那會讓 `scenarios:` 區塊裡混著 fixture，
P2 之後每一個「按 scenario 迭代」的地方都要靠人記得排除它們 ——
把區分留在人腦裡，不是留在系統裡。**id 字串長什麼樣沒有語意，它住在哪個區塊才有。**

## 6.3 v1 只交一個完整 scenario

**為什麼不是三個或五個**：

1. **這個 repo 自己立過這條規矩。** WS1 spec §4 拒絕在零個實例時訂 difficulty 公式，
   理由是「訂的是空氣」。同一條邏輯：Scenario Package 的格式現在也還沒有實例驗證過。
2. 多個會在格式未定案時同時寫多份，格式一改就改多份 —— 而那些是手寫檔案，
   沒有測試接得住（§0.2）。
3. **一個完整的 scenario 就足以驗證整條決策鏈。** 它會同時用到 §2.1 `expected_sources`、
   §2.2 detection 引用＋刻意缺口、§2.3 攻擊鏈、§3.1 真漏洞、§3.2 攻擊面宣告與內容鏈接、
   §3.3 單機縱深、§4.1 扁平 objective、§4.2 自由發揮、§5.1 flag 注入、§5.2 `reset_scope`。
   **十個決策，一個 scenario 全部踩過。** 任一決策是錯的，做這一個就會暴露；
   做三個只是把同一個錯誤犯三次。

## 6.4 紅隊工具：最小攻擊 image，WS2 宣告、WS6 供給

**硬約束**：`build-range.sh` 的 router **不做 SNAT/MASQUERADE**，forward chain `policy drop`，
只放行 `TARGET→MGMT` 與 `RED→TARGET:{80,3306}`。**Z-RED 沒有對外網**；
`attach-red.sh` 又是 `docker run --network none` 再接 OVS ——
**紅隊容器裡有什麼完全取決於 image，執行期一個套件都裝不了**。

現況預設 `RED_IMAGE=nicolaka/netshoot`，那是**網路除錯工具箱**，不是攻擊工具箱。
`kalilinux/kali-rolling` 只是覆寫選項，且其 base 幾乎不含工具，要 `apt` —— 在無網的 VLAN30 上裝不了。

**決定**：建一顆最小攻擊 image，內容由**當前 scenario 的攻擊鏈實際需要**決定，
不預裝「以後可能會用到的」。沿用既有的 `RED_IMAGE` 覆寫點當預設值。

**為什麼不給 Z-RED 對外網**：`policy drop` 加無 SNAT 是「六台紅隊 source IP 可分辨」
這條 G0 教訓的實作基礎，動它要重新檢查一票東西，而且會讓真攻擊工具連上外網。買不到對應價值。

**為什麼 netshoot 不夠**：§6.3 的攻擊鏈是「web 漏洞 → 撈到憑證 → 憑證開下一道門」，
而防火牆已放行 `3306` —— 下一道門很可能是 DB。沒有 client，憑證撈到了也用不了，
攻擊鏈第二段斷在**工具**上，而不是斷在玩家能力上。

**成本比 golden bake 低一個量級**：host 上一個 `docker build`（host 有網），
而且 `RED_IMAGE` 覆寫點**已經存在**。

**取捨要承認**：工具給得越少，訓練越硬（手工注入比 sqlmap 一鍵更接近真本事）；給得越多，
越可能變成「會不會用工具」的測驗。v1 偏保守 —— **寧可少裝，等某個 scenario 真的打不動再加**。
加是增量，拔掉已被依賴的工具才是重工。

---

# 7. 與已交付實作（#31 / PR #40）的衝突清單

`#31` 已於 2026-08-11 合併。以下六處必須以遷移票處理。

| # | 已交付 | 本檔要求 | 依據 |
|---|---|---|---|
| 1 | `scenarios/<id>.yaml` 單檔；`from_directory` 掃 `*.yaml` | `scenarios/<id>/metadata.yaml` ＋ `briefing.md` | §1.2 |
| 2 | `target: {type, service}` 單數扁平，值是 `vulnerable-app` | `targets:` 清單，含 `host` ＋ `surfaces` | §3.3、§3.2 |
| 3 | `telemetry: [app_log, http_metric]` 自由字串 | `expected_sources:`，值須對上 `config/scenario-sources.yaml` 的 `sources:` id | §2.1 |
| 4 | `detection: [SQLInjectionBurst]`，無存在性驗證、無缺口宣告 | 載入時驗證規則存在；新增刻意缺口的明示宣告 | §2.2 |
| 5 | 只有 `attack_mapping: [T1190]`（technique 清單） | 新增 `attack_chain:`（動作清單：id／technique／描述），餵 `#21` | §2.3 |
| 6 | 樣本是 `sqli-01`，指向 compose 的 `vulnerable-app` | `sqli-01` 判出局；樣本換成真 range 上的第一個真 scenario | §6.1、§6.2 |

外加一項新欄位：`reset_scope`（§5.2）。

**一個要保留的良好設計**：`src/range_core/scenarios.py` 的 pydantic model 全部是
`extra="forbid"` + `frozen=True`。這代表**每個新欄位都必須動 model 才進得來** ——
schema 是真的契約，不是鬆散的 dict。遷移時保持這個性質。

**另一個要保留的**：該模組的 docstring 明講「deliberately does not import from `purple`」。
§2.1 的檔案共享**不破壞這條** —— 是 `purple` 側讀 `scenarios/` 目錄，
不是 `range_core` 去 import `purple`，方向相反。

---

# 8. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼不在本檔決定 |
|---|---|
| briefing 具體寫什麼 | 內容創作，非架構。但 `metadata.yaml` / `briefing.md` 的切分已隱含答案：**攻擊鏈不給玩家看** |
| ~~Blue 計分門檻（`Contain < 60 sec`）放哪個欄位~~ | **已由 [WS3 spec §4.3](../ws3-blue-ops/spec.md) 關閉（2026-08-11）：不放進 scenario 檔**，平台級固定。裁決依據正是本檔 §0.1「藍方的東西 scenario 只引用不擁有」—— 該原則反過來否決了 `#37` 的既有假設 |
| flag 格式與防暴力猜測 rate limit | WS1 spec §6 已判為實作層細節 |
| objective 的遙測判定查詢怎麼寫 | `#33` 的事。WS2 只負責宣告判定型別 |
| Blue 側在 scenario 裡看到什麼 | WS3 未開始 |
| 刻意缺口與「自由發揮」的張力 | 紅隊沒去打那個沒覆蓋的洞，缺口就演示不出來。這是**內容設計**要處理的，不是平台加機制 |
| Scenario Marketplace／可散布封裝 | SA §16 歸在 V3 |

---

# 9. 決策總表

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 1 | Scenario Package 與 `#31` 的關係 | 一份檔；WS5 載入、WS2 作內容 | §1.1 |
| 2 | `expected_sources` 歸誰 | 搬進 scenario 檔，共享宣告檔而非跨區 API | §2.1 |
| 3 | 偵測規則 | 只引用＋明示缺口；`detection:` 不是分母 | §2.2 |
| 4 | 漏洞真實度 | 二分：可計分要真、fixture 維持模擬 | §3.1 |
| 5 | scenario 認哪些環境 | 只有真 range 算 | §6.1 |
| 6 | 靶機供給 | 單一多漏洞 golden，全開；scenario 宣告需求 | §3.2 |
| 7 | objective 前置依賴 | 不做，靠內容鏈接 | §4.1 |
| 8 | 多機橫向移動 | v1 單機縱深；只預付 `targets` 清單形態 | §3.3 |
| 9 | Flag 生命週期 | 每次 `range-up` 輪換，cloud-init 注入 | §5.1 |
| 10 | 攻擊鏈 vs Action Registry | 檔案是來源，DB 是凍結後的實例 | §2.3 |
| 11 | 靶機狀態回復 | 維持兩層；scenario 宣告 `reset_scope` | §5.2 |
| 12 | v1 交幾個 | 一個，做到完整 | §6.3 |
| 13 | 紅隊工具 | 最小攻擊 image；WS2 宣告、WS6 供給 | §6.4 |
| 14 | 落點與形態 | `scenarios/<id>/` 目錄 | §1.2 |
| 15 | 紅隊照劇本嗎 | 自由發揮；P2 需第四狀態 | §4.2、§4.3 |
| 16 | 既有五個 id | 字串不動，改住的區塊 | §6.2 |
| — | 貫穿原則（浮現，非提問） | 擁有 vs 引用；預付判準 | §0 |
