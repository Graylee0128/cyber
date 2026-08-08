# P1 Output Contract — 執行導覽

- **規格**：[spec.md](./spec.md)
- **決策與 trade-off**：[ADR 0001](../../docs/adr/0001-p1-output-contract.md)
- **上位計畫**：[purple_platform_plan.md](../../purple_platform_plan.md)
- **開發方式**：TDD
- **票**：[GitHub Issues](https://github.com/Graylee0128/cyber/issues)（2026-08-08 自本地 markdown 遷入）

> 本檔與 [spec.md](./spec.md) **刻意留在檔案裡** —— 契約與依賴敘事要能被 diff、
> 跟程式碼一起版控。只有票搬去 GitHub。

## 依賴圖

```text
01 ─→ 02a ─→ 02b ─→ 03 ─┬─→ 04 ─┬─→ 08
 時鐘   載具    紅燈     綠燈  │       └─→ 10
                              ├─→ 05
                              ├─→ 06
                              ├─→ 07
                              ├─→ 09
                              ├─→ 11
                              └─→ 12
```

`01 → 02a → 02b → 03` 是**單線**，沒有捷徑。03 之後扇出很寬，04–12 幾乎全部只依賴 03，可平行推進。

**03 是唯一的瓶頸。**

## 票

| # | 標題 | Blocked by | 核心紅燈 |
|---|---|---|---|
| ~~[01 · #1](https://github.com/Graylee0128/cyber/issues/1)~~ **done** | 時間同步基準線 | — | 41 tests，CI 綠 |
| [02a · #2](https://github.com/Graylee0128/cyber/issues/2) | 測試載具 | 01 | 載具自身會紅 |
| [02b · #3](https://github.com/Graylee0128/cyber/issues/3) | 第一條紅燈 | 02a | `sqli_produces_core_event`（刻意紅著交票） |
| [03 · #4](https://github.com/Graylee0128/cyber/issues/4) | SQLi → Core Event | 02b | 上條轉綠 |
| [04 · #5](https://github.com/Graylee0128/cyber/issues/5) | Receiver pure core | 03 | `technique_outside_whitelist_rejected` 等四條 |
| [05 · #6](https://github.com/Graylee0128/cyber/issues/6) | Alert lifecycle | 03 | `resolved_shares_event_id_with_firing` |
| [06 · #7](https://github.com/Graylee0128/cyber/issues/7) | Evidence resolver | 03 | `returns_context_window_not_single_line` |
| [07 · #8](https://github.com/Graylee0128/cyber/issues/8) | Source Registry 狀態機 | 03 | `expired_heartbeat_is_stale_not_absent` |
| [08 · #9](https://github.com/Graylee0128/cyber/issues/9) | Falco 作為 sensor | 03, 04 | `disabled_rule_shows_detection_gap` |
| [09 · #10](https://github.com/Graylee0128/cyber/issues/10) | Response 閉環 agent pull | 03 | `agent_pulls_no_inbound_to_target` |
| [10 · #11](https://github.com/Graylee0128/cyber/issues/11) | Prometheus／OTLP 路徑 | 03, 04 | `metric_alert_produces_core_event` |
| [11 · #12](https://github.com/Graylee0128/cyber/issues/12) | raw log 保留時段 | 03 | `raw_absent_outside_window` |
| [12 · #13](https://github.com/Graylee0128/cyber/issues/13) | 拓樸契約實測 | 03 | 四條契約腳本化 |

## 三條決定性的測試

其他測試證明功能可用，這三條證明**架構決策是對的**：

| 測試 | 在哪 | 它紅了代表什麼 |
|---|---|---|
| `disabled_rule_shows_detection_gap_not_visibility_gap` | 08 | 我們實質退回 D3，Falco 覆蓋範圍內的偵測缺口全部不可觀測 |
| `expired_heartbeat_is_stale_not_absent` | 07 | 設備故障被洗成「不在範圍內」，藍隊被系統性冤枉 |
| `agent_pulls_no_inbound_to_target` | 09 | `TARGET → MGMT` 單向被破壞，管理平面暴露到靶機網段 |

## 測試性質分級

不裝作每張票都能 red-green-refactor：

| 級別 | 票 | 特性 |
|---|---|---|
| **真 TDD** | 04、07 | 純函數，秒級，不需 docker |
| **半** | 05、06、11 | 邏輯可先測，整合需 docker |
| **契約測試** | 02b、03、08、09、10 | 只能對管路行為斷言，跑得慢 |
| **環境斷言** | 01、12 | 對真實網路／時鐘驗證，本質不是 TDD |

## 範圍界線

| 不做什麼 | 歸屬 |
|---|---|
| 網段建置（VLAN、macvlan、防火牆規則） | workstream 6 Range Infrastructure（12 只驗收） |
| `GET /evidence/{event_id}` HTTP endpoint | P2 Evaluation Engine（06 只交付 resolver 與 Alert Record） |
| Core Event 的下游消費 | workstream 5 Cyber Range Core（03 先落自有儲存 ＋ 可插拔 adapter） |
| coverage／MTTD 的計算與呈現 | P2 |
| Battleboard | Product UI |

## Decisions so far

- **2026-08-08** —— 三份契約定版（Core Event Schema／Source Registry／Evidence API），見 [spec.md](./spec.md)
- **2026-08-08** —— Falco 定位為 runtime sensor 而非 alert engine；Grafana Alerting 為唯一 alert engine（ADR ③）
- **2026-08-08** —— Core Event 移除 `evidence_ref`；改為 Core Event ＋ P1 Alert Record 兩份紀錄，用 `event_id` 對接（ADR ④）
- **2026-08-08** —— MTTR 終點＝response 生效；Grafana Resolved 另名 `containment duration`（ADR ⑦）
- **2026-08-08** —— 指標只留 `action coverage`，廢除 `Detection Rate`（ADR ⑨）
- **2026-08-08** —— 03 直接寫成 pure core ／ shell，不先做壞結構再重構
- **2026-08-08** —— **語言統一 Python**，含 09 的 response agent。理由：blast radius 涵蓋全部 13 張票，混語言會讓 02a 的載具要顧兩套。代價記在 09
- **2026-08-08** —— 其餘實作選型（測試框架、本機環境、Core Event 儲存、receiver 框架與 port）**由 02a 一次拍板**，不另寫 spec —— 那是實作選型不是契約，寫進 spec 只會過期
- **2026-08-08（票 01 順手定的）** —— pytest ＋ `src/` layout ＋ `pyproject.toml`；package 名 `purple`；pure core／I-O shell 以模組分離（`skew.py` 純、`probes.py` 碰 subprocess）。**02a 只需確認，不必重議**
- **2026-08-08** —— 前置檢查一律 **fail 不 skip**。skip 是前置檢查退化成永遠綠的標準路徑

## Fog

尚未有答案，會影響後續但不阻塞 P1：

- Grafana Alerting 是單點，掛了偵測全停 —— 已接受風險，補償方式未定
- 「Falco 根本沒寫那條 rule」仍會呈現為可見性缺口 —— 需 rule inventory，未排票
- 紅隊動作註冊的 UI 形狀 —— Product UI 決定，影響 P2 分母能否演練前固定
