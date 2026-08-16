# CH4 — Ghost in the System

> **淨新增 chain**（設計稿；實作是 CH4 子票）。
> 校園外皮：系統報修 / 診斷小工具吃系統指令，攻擊者在裡面住下來，關機也趕不走。

## 故事位置（Phase-1 Q5）

平台後台有個給系管用的「報修 / 診斷」小工具（ping 一下、看看服務活著沒）。它把使用者輸入直接塞進系統指令——攻擊者藉此拿到互動式 reverse shell，再種一條 cron 讓自己在重開機後依然回得來。這是攻擊者從「進得去」變成「趕不走」的一章。**Critical Phase：persistence。**

## Q1 玩家要完成什麼

透過指令注入取得 reverse shell，並建立一個持久化機制（cron / scheduled job），使其在 session 中斷後仍能重新取得存取（objective 標記證明持久化生效）。

## Q2 真正 attack surface 在哪

| Surface（模組） | 設計 |
|---|---|
| `system-command` | 報修 / 診斷功能：把使用者輸入拼進 `sh -c` 之類的系統指令（真指令注入，**非**現有 `/exec` 那種固定 marker fixture） |
| persistence | cron / at 寫入，對齊 T1053；由攻擊者植入、reset 時須清除 |

單主機：長在 `range-target` 的 `system-command` 模組。

> **與既有 fixture 的界線**：現有 `/exec`、`/uncovered` 是 Falco 教學 fixture，指令是**伺服器固定 marker、不吃使用者輸入**，且不掛 objective。CH4 是**使用者可控**的真指令注入，兩者不可混淆——實作票需確保 CH4 的注入面獨立於那些 fixture。

## Q3 平台如何知道完成

- **偵測類 objective**（telemetry）：`telemetry_signal.action_id` 指向 `command-injection-shell`（注入導致的 shell 執行）。
- **奪旗 objective**（submission，至多一個）：持久化生效後才可取得的標記（例如 cron 觸發後寫出的、range-up 注入的值），提交比對。

## Q4 Blue / Purple 應看到什麼

- Blue：**process / command-line telemetry**（非預期的 shell spawn、可疑 cmdline）+ **cron / scheduled job 變動**。
- Purple：coverage 顯示 T1059（執行）有覆蓋；T1053（持久化）是否有覆蓋由實作票決定——設計傾向**有覆蓋**，讓「植入持久化」被看見，與 FINAL 的完全 gap 相對。

## Q6 資訊揭露

- Red briefing：目標＝報修工具可疑、拿到 shell 並活下來；不透露注入點或持久化手法。
- Blue briefing：SOC 收到「後台工具起了非預期 shell + 排程被動過」的告警。
- Public：「Persistence Established」去識別事件。
- Purple / Instructor：完整注入 → shell → cron 鏈與 coverage。

## Q7 值得投影的 major event

- `major_event`（public）：Persistence Established（重大事件，Critical Phase 的高點之一）。
- `critical_alert`（role:blue）：非預期 shell + cron 變動（高嚴重度，配較強 cue）。
- `objective_complete`：`GHOST INSTALLED` / `有人在正式環境裡開趴。💀` —— 校園化 meme copy。

---

## 偵測預先指定（實作票須建）

| 項目 | 值（設計指定） |
|---|---|
| 父技術 | T1190 → **T1059**（Command and Scripting Interpreter，已存在）→ **T1053**（Scheduled Task/Job，已存在）——**無需新增** |
| Grafana alert title（新建） | `CommandInjectionTarget`（使用者可控輸入導致的 shell 執行）、`CronPersistenceTarget`（cron/at 變動） |
| Falco rule（新建） | `PurpleScope Command Injection Exec`（tags `[purplescope, T1059, execution]`；條件比照現有 `PurpleScope Command Exec` 但綁 CH4 專屬 marker，與 `/exec` fixture 區隔）、`PurpleScope Cron Persistence`（cron 檔 / crontab 寫入，tags `[purplescope, T1053, execution]`） |
| `expected_sources` | `[falco, alloy, response-agent]`（需 Falco process/exec 遙測） |
| `detection` | `[CommandInjectionTarget, CronPersistenceTarget]` |
| `intentional_gaps` | 無（本章刻意「持久化被看見」，當 FINAL 的對照組） |
| `reset_scope` | **`environment`**（reverse shell + cron 植入會弄髒靶機並跨 session 存活，必須環境級重置清除持久化） |

> 與其他章差異：CH4 的招牌是 **persistence + 跨 session 存活**，這是唯一一條「reset 不乾淨就會污染下一場」的章，`environment` reset 的必要性最強；telemetry 焦點在 process/command-line + 排程，和 CH1（DB）、CH2（檔案）、CH3（egress）都不同。

## 實作票 open questions（Phase 2）

- 指令注入面與現有 `/exec` fixture 的隔離（marker 命名、端點路徑）。
- reverse shell 的回連方式在 Z-TARGET ↔ Z-RED 網段是否可達、且被遙測抓到。
- cron 植入的清除在 `reset` 時是否確實（護欄測試：reset 後 cron 應消失）。
