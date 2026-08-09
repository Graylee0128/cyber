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
| ~~[02a · #2](https://github.com/Graylee0128/cyber/issues/2)~~ **done** | 測試載具 | 01 | CI 105 passed（含 PG）|
| ~~[02b · #3](https://github.com/Graylee0128/cyber/issues/3)~~ **done** | 第一條紅燈 | 02a | 紅→綠（03） |
| ~~[03 · #4](https://github.com/Graylee0128/cyber/issues/4)~~ **done** | SQLi → Core Event | 02b | CI 128 passed |
| ~~[04 · #5](https://github.com/Graylee0128/cyber/issues/5)~~ **done** | Receiver pure core | 03 | 四條全綠 |
| ~~[05 · #6](https://github.com/Graylee0128/cyber/issues/6)~~ **done** | Alert lifecycle | 03 | 共用 event_id、containment≠MTTR |
| ~~[06 · #7](https://github.com/Graylee0128/cyber/issues/7)~~ **done** | Evidence resolver | 03 | subagent；fake backend 證明可換 |
| ~~[07 · #8](https://github.com/Graylee0128/cyber/issues/8)~~ **done** | Source Registry 狀態機 | 03 | subagent；stale≠absent |
| [08 · #9](https://github.com/Graylee0128/cyber/issues/9) **管線 CI 綠/Falco 待大主機** | Falco 作為 sensor | 03, 04 | 真 exec→Falco→Core Event(T1059) + 決定性測試接真 Loki；Falco 本體 CI 起不了，待大主機 `--profile falco` |
| ~~[09 · #10](https://github.com/Graylee0128/cyber/issues/10)~~ **done** | Response 閉環 agent pull | 03 | expand→contract；agent pull 保單向 |
| [10 · #11](https://github.com/Graylee0128/cyber/issues/11) **✅ CI 綠** | Prometheus／OTLP 路徑 | 03, 04 | OTLP `:4317` push 取代 scrape；prometheus 移出 z-target，契約 2 首次真成立 |
| [11 · #12](https://github.com/Graylee0128/cyber/issues/12) **設定+量測done/磁碟待大主機** | raw log 保留時段 | 03 | window+快照純函式綠；loki retention 設定 + 量測腳本；磁碟數字待大主機 |
| [12 · #13](https://github.com/Graylee0128/cyber/issues/13) **腳本done/環境open** | 拓樸契約實測 | 03 | 腳本+邏輯綠；四區網段待環境（匯流點）|

## 三條決定性的測試

其他測試證明功能可用，這三條證明**架構決策是對的**：

| 測試 | 在哪 | 它紅了代表什麼 | 狀態 |
|---|---|---|---|
| `disabled_rule_shows_detection_gap_not_visibility_gap` | 08 | 我們實質退回 D3，Falco 覆蓋範圍內的偵測缺口全部不可觀測 | ✅ 綠 |
| `expired_heartbeat_is_stale_not_absent` | 07 | 設備故障被洗成「不在範圍內」，藍隊被系統性冤枉 | ✅ 綠 |
| `agent_pulls_no_inbound_to_target` | 09 | `TARGET → MGMT` 單向被破壞，管理平面暴露到靶機網段 | ✅ 綠（程式碼層；網段實測 #13）|

**三條決定性測試在程式碼／邏輯層全綠。** P1 的架構決策（D1、source registry、
agent pull 單向）都已被可執行的斷言證明，不只是文件宣稱。

## 部署測試（CD，2026-08-08 補上）

`integration.yml` + `docker-compose.yml` 全棧，CI 綠（**4 passed**）。補上原本只有
單元/契約 CI 的缺口，是 02b/03 的**真實端到端版本**。四條 E2E：

1. SQLi → Core Event（log 路徑全通）
2. 正常登入**不**觸發 SQLi 偵測（規則非恆真）
3. brute force → Core Event（metric 路徑：Prometheus `:9090` → PromQL 告警 → T1110）
4. 攻擊停止 → resolved 與 firing 共用 event_id（lifecycle）

- 單元/契約 job：`pytest -m "not integration"`（PG service container）
- 部署 job：`docker compose up --wait` → `pytest -m integration` → down
- **仍未涵蓋**：OTLP `:4317` push（現用 Prometheus scrape，非 OTLP）、Falco（08）、
  四區網段（12/#13）—— 需真 infra，compose 無法忠實模擬

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

### 票 02a 拍板的四項實作選型（2026-08-08）

- **儲存＝PostgreSQL**（否決 SQLite）。理由：① `timestamptz` 原生，SQLite 存字串會把票 01 剛擋掉的無時區問題請回來；② receiver 與 Evaluation Engine 是兩個行程，SQLite 單寫入者模型是錯的架構；③ Alert Record 的 `labels`／`fired_values` 是自由 JSON，要 `jsonb` 才查得動。schema 見 `src/purple/store/db.py`
- **測試一律需要 PG，不分層**（user 明確拍板）。用 autouse session fixture 強制。代價：**沒有 PG／Docker 就一條測試都跑不了**，換得沒有「本機綠、CI 紅」的落差。CI 由 service container 提供 PG
- **本機環境＝repo 根 `docker-compose.yml`**，目前只含 postgres。conftest 會在 PG 連不上時自動 `docker compose up -d postgres`（`PURPLE_AUTO_COMPOSE=1` 預設），達成「本機與 CI 同一指令 `python -m pytest`」
- **venv＝`.venv` ＋ `pip install -e ".[dev]"`**（票 01 暫裝進 user site-packages 的債還掉）
- **receiver 的 HTTP 框架與 port** —— **暫緩**，02a 用不到，留給 #4 定

## 完成 Z-MGMT 的路徑（2026-08-09，/to-tickets）

```text
#14 Evidence API 服務 ─→ #15 compose 網段隔離 ─→ #16 bring-up + handoff smoke ─→ #13 真網段
   ✅ done               ✅ done                   ✅ done ← Z-MGMT 軟體完成       (真 VLAN，env-gated)
```

> **「Z-MGMT 軟體完成」（2026-08-09，#16 里程碑達成）＝** 一個指令
> （`docker compose up -d --build --wait`）把六個 Z-MGMT 住戶健康起起來，
> 且 P1→P2 handoff 在 zone 內可執行地成立（Core Event 落地 → Evidence API 就該
> event_id 取回上下文窗）。有健康 gate（住戶未起就失敗）＋ handoff smoke 為證。
> **仍待 #13**：真 VLAN10/macvlan/firewall/deploy —— 那是**環境**完成，不是軟體完成。

- ~~**#14**~~ **done**：Evaluation Engine v0＝Evidence API `GET /evidence/{event_id}`。E2E 對**真 Loki** 取回 34 行上下文窗、依身分過濾、無 backend 洩漏。LokiBackend 已實作真查詢
- ~~**#15**~~ **done**：compose 定義四網段（z-app/z-mgmt/z-target/z-red），服務依 SA §12 歸位。靠 network membership 逼近隔離。交付的隔離證據：E2E 實測 **app(z-target) 連不到 postgres(z-mgmt)**，對照組 app 連得到同區 prometheus。`check_zone_assignments` 純函式驗區歸屬＋擋 compose 漂移；`verify_topology.py --compose` 可對解析後 config 驗。**compose 做不到、委派 #13**：契約 2 方向（prometheus 為 scrape 上 z-target 是刻意留的疤）、z-mgmt 未設 internal（會斷 host published port）、真 VLAN/macvlan/防火牆/六台 kali source IP
- ~~**#16**~~ **done**：Z-MGMT 軟體完成里程碑（定義見上）。補齊 prometheus（busybox wget）／grafana（alpine curl）healthcheck；loki 3.x 是 distroless（查證官方 Dockerfile），結構上無容器內 healthcheck，就緒改由 smoke 從 host 探查詢 API `:3100/loki/api/v1/labels` 補（不用 /ready——single-binary 的 /ready 帶 ring 延遲、與可查詢不一致）。`unhealthy_residents` 純函式做住戶健康 gate（真 TDD）；handoff smoke 證 Core Event 落地→Evidence API 就該 event_id 取回上下文
- **#16**：一鍵起整組健康住戶 ＋ P1→P2 handoff smoke＝**Z-MGMT 軟體完成**
- **#13**：真 VLAN10/firewall/deploy＝Z-MGMT **完全完成**，需真 infra
- 範圍界定：coverage/MTTD 計算與 Console 屬 P2 evaluation，**不在**「完成 Z-MGMT」內

## 環境收尾（Workstream 6 / #13）

**#13＝WS6 kickoff 兼環境驗收匯流點**。部署形態：**單主機巢狀**（選型：混合
VM+容器 + Open vSwitch，user 2026-08-09 拍板）。切成 4 個 slice，見
`scripts/range/README.md`：

- **Slice 1 ✅ CI 綠（2026-08-09）**：OVS 四區 802.1Q VLAN（10/20/30/40）+ netns
  節點 + router + **nftables 真方向性單向防火牆**。`range.yml` 在 GitHub runner
  （免 nested virt）實測全綠：契約 1 TARGET→MGMT 通、契約 2 MGMT→TARGET 反向不通、
  契約 3 RED→MGMT deny、六台 red source IP `10.167.30.11~16` 可分辨（router 不做
  SNAT）。這是 **#15 委派出來的契約 2 方向性**——membership 做不到、nftables 真做。
- **Slice 2a ✅ 大主機綠（2026-08-09）**：靶機換成**真 VM**（KVM/libvirt，Ubuntu
  noble cloud image）接 OVS VLAN20（靜態 IP `10.167.20.10`）。免 SSH 自驗：VM serial
  console 導檔 + cloud-init 開機跑契約 1。實測 **契約 1 VM→MGMT `:3100/:9090/:4317`
  通、契約 2 MGMT→VM 反向被 nftables 擋**。這是 netns → 真 VM 的關鍵一躍（混合模式）。
  腳本兩道跨主機防呆：`curl -C -` 續傳到 `.part`＋`qemu-img check` 完整性把關——
  半截 image 永遠進不到 VM（否則 overlay 讀壞區塊 → EXT4 I/O error → kernel panic，
  症狀像網路問題其實是磁碟；此坑已於大主機實遇並修掉）。
- **Slice 2b-① ✅ 大主機綠（2026-08-09）**：Falco（modern-eBPF / CO-RE）在真 VM 內裝
  起並抓到已知動作（讀 sentinel 檔 → 自訂 rule 開火 → journald 撈到 `PURPLESCOPE-
  FALCO-HIT`）。這把 **#9** 從「決定性測試綠、真 Falco 待環境」推進到**真環境能力已證**：
  modern eBPF 在此 nested VM kernel 掛得起來、抓得到 syscall。免 SSH：cloud-init 自驗
  印 console，host 判定。走 NAT 取 internet 裝 Falco（VLAN20 無對外網是刻意）。
  踩到並記錄的坑：cloud-init `runcmd` 用 `/bin/sh`(dash) 跑，`>(...)` process
  substitution 會 `redirection unexpected` → 邏輯改放 `#!/bin/bash` 腳本、runcmd 只呼叫。
- **Slice 2b-② ✅ 管線 CI 綠（2026-08-09）**：Falco→Alloy→Loki→Grafana→Core Event(T1059)
  走 **compose**（非 VM）。Falco 加成 z-target 服務（privileged/modern-eBPF，profile
  gated——CI 起不了 Falco，`docker compose --profile falco up` 才拉）；靶機 `/exec` 生
  shell → Falco 抓 execve → 自訂 rule → Grafana FalcoCommandExec → webhook → T1059。
  契約測試（手餵 webhook）+ 決定性測試**真環境版**（telemetry_present 接真 Loki 查詢）。
  CI 驗管線本體綠；Falco 本體待大主機 `--profile falco` + `PURPLE_FALCO_ENABLED=1`。
- **Slice 3-① ✅ CI 綠（2026-08-09）**：OTLP `:4317` push 取代 scrape（#11）。靶機用 OTLP
  push `ssh_failed_logins` → Alloy `otelcol.receiver.otlp` → `prometheus.remote_write`；
  Prometheus 開 `--web.enable-remote-write-receiver`、**移出 z-target**——契約 2 那道
  MGMT→TARGET scrape 疤在 compose 首次真消失（新測試 app 連不到 prometheus 為證）。
  brute-force metric E2E（T1110）經 OTLP 路徑仍綠。
- **Slice 3-②（#12）**：loki-config 開 compactor retention（168h）+ 具名 lokidata volume；
  `measure-log-retention.sh` 量 app/falco 行數 + Loki volume 磁碟 + retention。磁碟數字待大主機。
- **Slice 4（腳本齊，待大主機驗）**：`range-up.sh` 一鍵 IaC（組 Slice1+2a，`--with-red`
  六台 kali 接 VLAN30、`--with-falco` 用 golden 靶機）；`build-golden-target.sh` 烤 Falco
  進 image 跑無網 VLAN20；`range-reset.sh` Reset。**不在 CI**（巢狀虛擬化）。

仍只能在真環境（大主機）收的：真 VM、Falco eBPF、OTLP push、生產規模數字。
GitHub runner 不支援巢狀虛擬化，故 Slice 2+ 的真 VM 部分不在 CI。

## Fog

尚未有答案，會影響後續但不阻塞 P1：

- Grafana Alerting 是單點，掛了偵測全停 —— 已接受風險，補償方式未定
- 「Falco 根本沒寫那條 rule」仍會呈現為可見性缺口 —— 需 rule inventory，未排票
- 紅隊動作註冊的 UI 形狀 —— Product UI 決定，影響 P2 分母能否演練前固定
