# CH1 — The Breach

> **現有 chain**（`scenarios/shopdb-credential-pivot`）。本章不是新設計，而是把既有 scenario 記錄成 Campaign 的第一章、當其餘四章的 schema / contract 基準。
> 校園外皮：福利社線上商城被撬開。

## 故事位置（Phase-1 Q5）

明湖科大福利社的線上商城，把「不該放在一起的東西塞進同一套資料庫」。攻擊者從公開的商品目錄下手，撈出後台 DB 憑證，再拿它開第二道門，讀走鎖在保險庫裡的東西。這是整場入侵的起點——**Initial Incident**。

## Q1 玩家要完成什麼

從商店主機的保險庫取得 flag 並提交。光靠第一段 SQLi 撈不到 flag（web 帳號對 vault 無 grant），必須先撈出 `dbadmin` 憑證、再以它直連 DB。

## Q2 真正 attack surface 在哪

| Surface | 說明 |
|---|---|
| `product-sqli` | `/product?id=` 字串拼接進 SQL，UNION-based 可注入（可計分面） |
| `mysql-3306` | 第二道門：`dbadmin@'%'` TCP 可達，`webapp@localhost` socket-only（授權邊界在 `seed.sql`） |

單主機縱深：`targets` 是清單但只有一台 `range-target`。

## Q3 平台如何知道完成

- **偵測類 objective** `detect-catalog-sqli`（telemetry，100pt）：偵測到 catalog SQLi 就得分，`telemetry_signal.action_id = sqli-extract-credentials`，靠 action_id 關聯、不靠時間窗。
- **奪旗 objective** `capture-vault-flag`（submission，200pt）：flag 由 range-up 當場注入，未經利用取不到，提交比對。

## Q4 Blue / Purple 應看到什麼

- Blue：第一段 SQLi 有偵測覆蓋，Core Event 進 SOC timeline。
- Purple：coverage 表顯示 T1190 有覆蓋、**T1078 無覆蓋**（撈到的憑證直連 :3306 沒有任何 Grafana 規則）。

## Q6 資訊揭露

- Red briefing 只給目標與規則，**不含攻擊路徑**（`briefing.md` 慣例）。
- Public / Battleboard：「商城遭初始入侵」級別的去識別事件。
- Purple / Instructor：完整 SQLi → pivot 鏈與 coverage。

## Q7 值得投影的 major event

- `major_event`（public）：Initial Access Detected（去識別）。
- `objective_complete`（role + public）：撈到憑證 / 奪旗。
- `critical_alert`（role:blue + public 去識別）：SQLi burst firing。

---

## 偵測預先指定（現況，已落地）

| 項目 | 值 |
|---|---|
| 父技術 | T1190（Exploit Public-Facing App）→ T1078（Valid Accounts） |
| Grafana alert title | `SQLInjectionBurstTarget`（`deploy/grafana/provisioning/alerting/rules.yaml`） |
| Falco rule | —（本章走 app-log SQLi metric，不靠 Falco） |
| `expected_sources` | `[alloy, response-agent]` |
| `detection` | `[SQLInjectionBurstTarget]` |
| `intentional_gaps` | `[T1078]` —— **DETECTION_GAP**：遙測看得到（app log 有連線）但無告警 |
| `reset_scope` | `exercise`（攻擊鏈全程唯讀 SELECT，不弄髒靶機） |

> gap 歸類示範：T1078 是**偵測缺口**（看得到卻沒偵測到），不是可見性缺口（根本沒看到）。這是 ADR ③ 要能分辨、也是 CH5/FINAL detection-gap 教學的原型。
