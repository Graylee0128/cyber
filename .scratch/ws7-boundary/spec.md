# Spec — WS7 邊界層（Product UI 的非 UI 部分）

- **Status**: draft（定案後比照 P1 遷出至 `docs/`；見 [cyber/CLAUDE.md](../../CLAUDE.md) 資料夾約定）
- **決策依據**：2026-08-11 grilling session（四題，本檔為落地記錄）
- **上位文件**：[SA](../../資安攻防平台_系統架構設計文件_v0.1.md) §4.1、§5、§12.3；[purple_platform_plan.md](../../purple_platform_plan.md) §3.7、§4.1
- **上游依賴**：[WS1](../ws1-game-design/spec.md)、[WS5](../ws5-range-core/spec.md)、[WS2](../ws2-scenario-target/spec.md)、[WS3](../ws3-blue-ops/spec.md)

## 為什麼 WS7 要拆成兩半

SA §4.1 寫「**WS7 不該早做** —— 它要顯示的數字由 WS4-P2 與 WS5 產生；數字還不存在時做 UI，
只能做出假資料畫面」。**那句話對，但它只管畫面那一半。**

前面幾輪 grilling 陸續把四件**不是 UI** 的責任塞進了 Console：

| 來源 | 塞給 WS7 的責任 | 性質 |
|---|---|---|
| [WS3 §5.1](../ws3-blue-ops/spec.md) | Blue SOC Console **這個服務**持有 blue clearance | 安全邊界 |
| [WS3 §5.2](../ws3-blue-ops/spec.md) | Console 是封鎖路徑的入口 | 動作路徑 |
| [WS5 §2.3](../ws5-range-core/spec.md) | 「全部重來」由 Instructor Console 依序呼叫兩層 reset | 編排 |
| `#26` | 從 Z-APP 對 Z-MGMT 發 raw query **必須失敗**（要實測） | 權限邊界 |

**Console 在這個架構裡不是顯示層，是身分、動作、編排、權限的落點。**

所以 WS7 切成：

| 半邊 | 何時做 | 理由 |
|---|---|---|
| **邊界層**（本檔） | **現在** | 已有 `#36`／`#49`／`#50`／`#51` 四張票踩在上面，不定就是讓實作者猜 |
| 畫面層 | 等 WS4-P2／WS5 產出數字 | SA §4.1 的理由成立 |

---

# 1. 遮蔽規則住在共用契約套件

## 1.1 決策

新建 `src/` 下的第三個套件（與 `purple`、`range_core` 並列）承載**跨 context 的契約規則**：

| 內容 | 現在住哪 |
|---|---|
| `VISIBILITY_BY_EVENT_TYPE`（事件級對照表） | `purple/harness/schema.py` |
| `CALLER_CLEARANCE` / `VISIBILITY_RANK`（身分階序） | `purple/evidence/resolver.py` |
| **欄位級遮蔽表**（哪些欄位對哪些 clearance 露出） | **還不存在**，[WS3 §2](../ws3-blue-ops/spec.md) 要新增 |

`purple` 與 `range_core` **都 import 它，但仍不互相 import**。

> 打包不需額外設定：`pyproject.toml` 是 `[tool.setuptools.packages.find] where = ["src"]`，
> 放進 `src/` 就會被找到。

## 1.2 為什麼不是各自實作

面向玩家的事件有**兩個出口**，分屬兩個 bounded context：

| 出口 | 在哪 | 誰的程式 |
|---|---|---|
| `SSE /api/events/live`（`#36`） | Z-APP | `range_core` |
| `GET /evidence/{event_id}`（`#51`） | Z-MGMT | `purple.evidence` |

兩邊都要遮，而 `range_core` 刻意不 import `purple`。若各自實作：

> **漏遮一個欄位不會有任何錯誤訊號**，只會讓 `Identify Technique +100` 悄悄變成參與獎，
> 而且要等到有人在演練中發現分數不對才會知道。

「共用測試向量」（比照 `tests/range/test_zones_env.py` 對 `zones.env` 的機器檢查）是認真考慮過的替代方案，
**但它的殘餘風險正好落在最可能的漏法上**：向量擋得住「已經想到要遮的欄位」，
擋不住「新增了一個該遮但沒人想到的欄位」。共用程式碼讓漂移**結構上不可能**，不是靠測試涵蓋率。

## 1.3 搬遷成本（實測，不是估計）

**關鍵事實**：大多數測試經 `purple.harness` 這個 **re-export shim** 進來，不是直接 import `schema.py`。

| 符號 | production 使用者 | 測試**直接** import |
|---|---|---|
| `assert_core_event` / `expected_visibility` / `VISIBILITY_BY_EVENT_TYPE` | `harness/__init__.py`、`receiver/core.py` | **1 個**（`test_field_governance.py`）；另 11 個測試檔走 re-export |
| `CALLER_CLEARANCE` / `VISIBILITY_RANK` | `evidence/service.py`、`evidence/__init__.py` | **1 個**（`test_evidence_logic.py`） |

實際要動 **~4 個 production 檔 ＋ ~2 個測試檔**，全部是 T1 純函式，**CI 驗得到**。

**搬遷紀律**：`purple.harness` 與 `purple.evidence` 的 re-export **必須保留**，
否則 blast radius 會從 6 個檔炸成 20 個。

## 1.4 為什麼不收斂成單一出口

「Evidence 也經 Range Core 轉」會與 [WS3 §6](../ws3-blue-ops/spec.md)「Console 直連受管端點」矛盾，
且讓 WS3 §5.2 已經承認的「Range Core 是單點」再擴大到整條調查路徑。

---

# 2. 服務身分：部署時注入的 token

## 2.1 決策

Console 的 identity 由**部署時注入的服務 token** 建立，API 查表換成 clearance。

**`CALLER_CLEARANCE` 的查表模型一個字都不用改** —— 只是 identity 的來源從
「呼叫端自填字串」變成「token 換出來的值」。

## 2.2 為什麼需要這一題

[WS3 §5.1](../ws3-blue-ops/spec.md) 把整個藍隊身分模型押在「**服務**持有 clearance」上，
而 Evidence API 的保證是「clearance 由 `identity` 決定，**呼叫者無法自報**」
（[resolver.py](../../src/purple/evidence/resolver.py)）。

**目前沒有任何機制實現那句話。** 不定這一題，`#51` 的驗收條件
「一條測試證明呼叫者無法自報 clearance」**寫得出來但做不出來**。

## 2.3 威脅模型（決定了為什麼 token 就夠）

要防的是：**紅隊玩家的瀏覽器直接打 API 拿到 blue 級資料**。

不防的是：Z-APP 內部的橫向移動 —— 那一區只住我們自己的服務。

| 方案 | 擋得住威脅嗎 | 成本 |
|---|---|---|
| **服務 token** | ✅ | 幾個環境變數 |
| mTLS | ✅ | PKI ＋ 憑證輪替 —— 為不存在的威脅付錢 |
| 來源 IP | ❌ | 任何跑在 Z-APP 的東西都能冒充，**而紅隊的 Player Portal 也在 Z-APP** |

## 2.4 與既有做法一致

token 走環境變數注入，與 `deploy/` 既有的設定注入方式相同，不引入新的機密管理機制。

**升級觸發條件**：Z-APP 開始承載非自有服務（多租戶、第三方整合）時，升級成 mTLS。
屆時 `CALLER_CLEARANCE` 仍不用改。

---

# 3. 所有玩家動作進 Range Core，單一入口

## 3.1 決策

flag 提交、hint 索取、`acknowledge`／`classify`／`contain`／`resolve`／`dismiss`、
instructor 控制 —— **全部先進 Range Core**，落地成記錄後，再由 Range Core 決定要不要派送。

這是 [WS3 §5.2](../ws3-blue-ops/spec.md) 的推廣：那裡只定了 `contain`，本節推廣到所有動作。

## 3.2 為什麼

1. **所有動作都要先落地成記錄**，因為計分要用。[WS5 §1.3](../ws5-range-core/spec.md) 的分數是
   `f(完成的 objective, 用掉的 hint)` 即時推導 —— 動作記錄散在多處，推導就要跨服務撈資料。
2. **避免雙寫的部分失敗**（WS3 §5.2 已論證）：兩個 zone 沒有交易保證。

## 3.3 必須寫進 spec 的推論

**Range Core 因此成為所有玩家動作的單點**，不只是封鎖路徑。

這比 WS3 §5.2 承認的範圍更大 —— 那裡只說「Range Core 是封鎖路徑的單點」。
現在的正確敘述是：**Range Core 掛了，整場演練停擺**（紅隊的攻擊除外 ——
紅隊是繞過平台直接打靶機的，見 WS5 §3.1）。

在單機 range 上可接受，但要寫下來，不是等它發生。

---

# 4. Instructor：唯讀 ＋ 生命週期控制，不得改分數

## 4.1 決策

`instructor` clearance 最高（3），但權限是**唯讀 ＋ 生命週期控制**：

| 能做 | 不能做 |
|---|---|
| 開演、中止、reset（兩層編排，WS5 §2.3） | **改分數** |
| 看到全部 visibility 層級的資料 | **代判 objective 完成** |
| 產出報告 | **覆寫藍隊的判讀結果** |

## 4.2 為什麼不能改分數

[WS5 spec §1.3](../ws5-range-core/spec.md) 已定案「**分數是推導出來的，不存欄位**」，
理由是「存起來的分數會**靜默地漂** —— 欄位寫 450、objective 加總是 500，沒有任何機制會發現」。

**「人工覆寫」需要一個可變欄位，那正是該決策要消滅的東西。** 允許 instructor 改分數
等於把 WS5 §1.3 廢掉。

判錯了的正確做法是**修 scenario 或修規則然後重跑**，不是改分數 ——
與整個專案「不接受宣稱、只接受證據」的基調一致。

## 4.3 代價

演練中出現爭議（玩家確實做到了但遙測沒抓到）時，現場無法補救，只能重跑或記在報告裡。

**這個代價指向 WS2 的內容品質**：遙測抓不到玩家做到的事，是 scenario 設計或偵測覆蓋的缺陷，
應該在 [WS2 §3.1](../ws2-scenario-target/spec.md) 的可計分攻擊面設計時就避免，
而不是靠教官在現場補分掩蓋。

---

# 5. 對既有票的影響

| 票 | 什麼事 | 依據 |
|---|---|---|
| `#49` | 遮蔽實作落在**共用契約套件**，不是各自實作、也不是 Console 前端 | §1 |
| `#36` | SSE 的欄位級遮蔽 import 共用契約，**不得自行實作一套** | §1 |
| `#51` | Console 身分＝服務 token；「呼叫者無法自報」的測試現在做得出來了 | §2 |
| `#50` | 五個動作的入口統一在 Range Core | §3 |
| `#38` | Instructor 的 reset 編排權限確認；**不含改分數** | §4 |
| 新票 | 契約套件的建立與搬遷（`purple` 的 re-export 必須保留） | §1.3 |

---

# 6. 範圍界線（本次刻意不決定的）

| 項目 | 為什麼 |
|---|---|
| 四個 Console 的畫面設計 | WS7 畫面層，等 P2／WS5 產出數字（SA §4.1） |
| Console 的技術選型（框架、SSR 與否） | 實作層 |
| Battleboard 呈現規則 | [plan §3.9](../../purple_platform_plan.md) 已定（公開層用狀態不用比率） |
| token 的輪替與撤銷 | 單機 range 上不需要；Z-APP 承載非自有服務時連同 mTLS 一起處理（§2.4） |
| 多租戶下的身分模型 | SA §16 歸在 V3 |

---

# 7. 決策總表

| # | 決策 | 選擇 | 章節 |
|---|---|---|---|
| 1 | 遮蔽規則住哪 | 共用契約套件（`purple` 與 `range_core` 都 import，仍不互相 import） | §1 |
| 2 | Console 怎麼證明身分 | 部署時注入的服務 token | §2 |
| 3 | 玩家動作的入口 | 全部進 Range Core，單一入口 | §3 |
| 4 | Instructor 權限範圍 | 唯讀 ＋ 生命週期控制，不得改分數 | §4 |
