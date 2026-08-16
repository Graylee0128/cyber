# CH2 — Foothold

> **淨新增 chain**（設計稿；實作是 CH2 子票）。
> 校園外皮：作業 / 社團海報上傳功能被繞過，傳上去的「海報」其實是後門。

## 故事位置（Phase-1 Q5）

攻擊者摸到後台後，發現平台開放學生上傳社團海報與作業附件。上傳檢查形同虛設——一張「海報」被偷渡成可執行的 web shell，攻擊者從此在伺服器上有了立足點（foothold），再利用一個設定不當的權限爬到 root。**Escalation 的第一步。**

## Q1 玩家要完成什麼

繞過上傳限制放上 web shell、取得目標主機上的命令執行，再透過本機提權拿到 root（讀取只有 root 能讀的 objective 標記）。

## Q2 真正 attack surface 在哪

| Surface（模組） | 設計 |
|---|---|
| `file-upload` | 海報/作業上傳端點：MIME / 副檔名 / magic-byte 檢查其中一項可繞過（真漏洞，非 mock），落地位置在 web 可執行路徑 |
| 提權面 | 一個刻意設定不當的本機提權向量（SUID / sudo 設定 / capability 其一，對齊 T1548；具體向量由實作票在單機上選定並確保可重現、可 reset） |

單主機：長在 `range-target` 的 `file-upload` 模組，不新增主機。

## Q3 平台如何知道完成

- **偵測類 objective**（telemetry）：web shell 落地後的執行，`telemetry_signal.action_id` 指向 attack_chain 的 `deploy-webshell` 或 `local-privesc`（實作票擇一或各一）。
- **奪旗 objective**（submission，至多一個）：root-only 標記檔內容提交比對。
  - 契約限制：一條 scenario **最多一個 submission objective**（共享環境 flag 限制，#45）。

## Q4 Blue / Purple 應看到什麼

- Blue：**檔案完整性**（web 目錄出現新可執行檔）+ **auditd / Falco exec**（web 服務帳號 spawn shell）。
- Purple：coverage 表顯示 T1505（web shell 落地）與 T1548（提權）是否有覆蓋；設計上**兩者都要有覆蓋**，讓本章成為「攻擊被完整看見」的對照組（與 FINAL 的 detection gap 相對）。

## Q6 資訊揭露

- Red briefing：目標＝上傳功能可疑、拿到 root；不透露繞過手法或提權向量。
- Blue briefing：SOC 收到「web 目錄出現非預期檔案 + 服務帳號起了 shell」的異常。
- Public：「Foothold Established」去識別事件，不揭露是上傳漏洞。
- Purple / Instructor：完整鏈與 coverage。

## Q7 值得投影的 major event

- `major_event`（public）：Foothold Established。
- `critical_alert`（role:blue）：web 服務帳號 spawn shell（高嚴重度，配短 SFX）。
- `objective_complete`：`ROOT ACCESS ACQUIRED` —— 可掛校園化 meme copy（見 README tone）。

---

## 偵測預先指定（實作票須建）

| 項目 | 值（設計指定） |
|---|---|
| 父技術 | T1190 → **T1505**（Server Software Component，web shell）→ **T1548**（Abuse Elevation Control Mechanism，提權）——皆已在 `techniques.yaml`，**無需新增** |
| Grafana alert title（新建） | `WebShellUploadTarget`（檔案落地 / web 服務起 shell）、`LocalPrivescTarget`（提權跡象）—— PascalCase，比照現有命名 |
| Falco rule（新建） | `PurpleScope WebShell Exec`（web 服務帳號 spawn shell，tags `[purplescope, T1505, persistence]`）、提權則對齊現有 `PurpleScope Blue Seat Sudo Attempt` 的 T1548 pattern |
| `expected_sources` | `[falco, alloy, response-agent]`（本章需要 Falco exec 遙測，比 CH1 多 `falco`） |
| `detection` | `[WebShellUploadTarget, LocalPrivescTarget]`（兩段皆有覆蓋） |
| `intentional_gaps` | 無（本章刻意「全覆蓋」，當 FINAL detection gap 的對照組） |
| `reset_scope` | **`environment`**（web shell 落地 + 提權會弄髒靶機檔案系統，非唯讀，需環境級重置） |

> 與 CH1 的差異刻意拉大：CH1 唯讀 / `exercise` reset / 走 app-log metric；CH2 寫檔 / `environment` reset / 走 Falco exec + 檔案完整性。attack surface、reset 語意、遙測來源三者都不同，不是同漏洞換皮。

## 實作票 open questions（留給 CH2 子票驗證，Phase 2）

- 提權向量的具體選擇（SUID vs sudo vs capability），需在單機上可重現且 reset 後乾淨。
- web shell 落地路徑與 web 服務執行身分，需真的可執行且遙測抓得到。
- 上傳繞過的具體漏洞（副檔名 / MIME / magic-byte / 路徑），需真漏洞非 mock。
