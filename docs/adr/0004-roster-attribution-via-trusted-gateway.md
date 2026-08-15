# ADR 0004 — 名冊歸屬改由單一可信 gateway 代宣告來源 IP

- **狀態**：Accepted
- **日期**：2026-08-15
- **決策者**：gray
- **產生方式**：#126 item 4 實作過程中，原方案撞上既有測試而改道
- **影響範圍**：[ADR 0003](0003-ws8-range-core-admission.md)「Trust-boundary consequence」一節（**取代**）、`src/range_core/api.py` 的 `_source_ip`、`src/admission/api.py`、`deploy/ui/default.conf.template`、[WS8 spec](../../.scratch/ws8-event-control/spec.md) §5.3
- **實作**：#126 / PR #134（`8dfa5ba`）

---

# 1. 背景

flag 提交與 hint 用來源 IP 當名冊的鍵（[ADR 0003](0003-ws8-range-core-admission.md)、WS1 spec §1.3
的個人計分），而 `_source_ip` **刻意不信任** `X-Forwarded-For` —— 信了就等於讓一個紅隊玩家
用別人的身分提交 flag。

ADR 0003 因此明文寫下：

> gameplay retains the existing direct Kali-to-Range Core trust boundary. A reverse proxy or
> NAT in that path would still break attribution closed with HTTP 403; **this ADR does not
> authorize such a topology.**

但 Product UI（#75）落地後，Player Portal 的提交確實是經 gateway 進來的 —— Range Core 看到的
是那台 nginx 的位址，於是**每一次提交都 403**。ADR 0003 預言的失敗模式成真了，而它給的出路
（「不要有反向代理」）與已經交付的 Portal 相衝。

# 2. 決策

**只信任一個呼叫者，而不是開始相信某個標頭。**

| 環節 | 做法 |
|---|---|
| 誰知道座位的來源 IP | Admission。新增 `GET /admission/auth/seat`，回答「這個 session 坐哪台機器」 |
| 誰代為宣告 | Product UI 的 gateway，對 `/gw/red/core/api/{submissions,hints}` 先 `auth_request` 問上一列，把答案放進 `X-Seat-Source-Ip`，並**丟棄客戶端送進來的同名標頭** |
| 誰決定要不要信 | Range Core。只在 TCP peer 屬於 `RANGE_CORE_TRUSTED_EDGE_HOST` 解析出的位址時才採信該標頭；未設定該變數時誰都不信（fail closed） |

值取 `endpoints[0].host`，與領位時 `AdmissionService.ready()` 交給 Range Core 的 `source_ip`
是同一個取法 —— 兩邊若各取各的，在多端點座位上會悄悄對不起來。

## 2.1 為什麼這不是 ADR 0003 拒絕的那個設計

ADR 0003 拒絕的是「相信一個任何人都能設的標頭」。這裡的標頭只在 **TCP peer 檢查通過之後**
才被讀取。紅隊玩家直連 Range Core 並自己塞 `X-Seat-Source-Ip`，拿到的仍是自己的 peer 位址
—— 因為他的 peer 是自己那台 kali，不是 gateway。**要偽造它，前提是已經成為 gateway 本身。**

標頭名字刻意不用 `X-Forwarded-For`：那個名字全世界的 proxy 和不少客戶端都會設，一個掛在
那底下的值說明不了是誰放的。

## 2.2 為什麼是 Product UI gateway，不是 Z-EDGE

第一版寫在 Z-EDGE（直覺上它才是「玩家的入口」），被既有測試
`tests/deploy/test_edge_access.py::test_edge_config_contains_no_database_or_credential_material`
擋下。理由是 [WS8 spec](../../.scratch/ws8-event-control/spec.md) §5.3：**Z-EDGE 零憑證**，
被打下時不該連帶交出任何 token。

而代宣告來源 IP 必然需要握有 Range Core 的服務 token（Range Core 的每條端點都要 Bearer）。
把 token 放上 Z-EDGE 等於為了修一個 403 去破壞一條跨世代的邊界。

Product UI gateway 沒有這個矛盾 —— 它**本來就依設計持有全部服務 token**
（`deploy/ui/default.conf.template` 的檔頭：「服務 token 全部注入在這裡，不進瀏覽器」）。
改走這裡沒有讓任何主機多拿到一份秘密，`deploy/edge/` 一個字都不用動。

# 3. 對 ADR 0003 的影響

[ADR 0003](0003-ws8-range-core-admission.md) 的「Trust-boundary consequence」一節**由本 ADR 取代**。
具體地說，下列敘述不再成立：

| ADR 0003 的敘述 | 現況 |
|---|---|
| 「submissions and hints still resolve that association **exclusively** from TCP peer」 | 多了一個例外：peer 是可信 gateway 時讀 `X-Seat-Source-Ip` |
| 「`X-Forwarded-For`, `X-Real-IP`, and caller-supplied identity fields grant no attribution」 | **仍然成立**，未變 —— 新標頭不是 caller-supplied，它在 peer 檢查後才被讀 |
| 「A reverse proxy in that path would still break attribution closed with HTTP 403; this ADR does not authorize such a topology」 | **已授權**該拓樸，但僅限一台、且該台必須列在 `RANGE_CORE_TRUSTED_EDGE_HOST` |

ADR 0003 的其餘部分（prepare/publish/revoke 生命週期、`admission` 服務角色、Blue 不進
source-IP 計分、Z-RED 位址空間）**全部不變**。

# 4. 後果

- **部署新增必要設定**：`RANGE_CORE_TRUSTED_EDGE_HOST`。沒設時 Portal 的提交會退回
  403（與修復前同樣的症狀），這是刻意的 fail closed —— 不設定的結果是「功能不通」，
  不是「任何人都能宣告來源 IP」。
- 該變數的值是**主機名**，每個請求即時解析而非啟動時快取：容器重啟會換位址，快取會
  造成看起來像名冊 bug 的失效。
- 信任面從「零個代宣告者」變成「一個」。這是實質擴張，代價由 §2.1 的 peer 檢查與
  `tests/range_core/test_api_scoring.py::TestSeatSourceIpHeaderIsOnlyTrustedFromTheEdge`
  的四條測試（含三條負向）承擔。
- Z-EDGE 的零憑證性質**未受影響**，`tests/deploy/test_edge_access.py` 仍是它的看守者。
- 藍隊 session 走這條路徑時不帶標頭（藍隊不做個人計分，沒有名冊歸屬），Range Core
  因此退回看 peer —— 對藍隊而言行為與修復前完全相同。
