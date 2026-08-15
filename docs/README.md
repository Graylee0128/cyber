# Cyber 文件導覽

四種文件，四種讀者。**加新文件前先確認它屬於哪一類**——分錯就會長成沒人維護的
documentation monolith。

| 類型 | 回答的問題 | 位置 | 讀者 |
|---|---|---|---|
| **Participant Guide** | 玩家怎麼參加 | [`participant-guide/`](./participant-guide/README.md) | 紅藍隊玩家 |
| **Operator Guide** | 工作人員怎麼把它跑起來、辦完一場活動 | [`operator-guide/`](./operator-guide/README.md) | Instructor、Purple、現場工作人員 |
| **Technical Handbook** | 整套系統現在怎麼運作 | [`technical-handbook/`](./technical-handbook/README.md) | 開發與維運工程師 |
| **Architecture** | 架構決策與跨元件契約 | [`architecture/`](./architecture/)、[`adr/`](./adr/) | 工程師、審查者 |
| **Spec** | 這個功能**應該**怎麼做 | `.scratch/<workstream>/spec.md` | 實作者 |

界線的判準：

- 講**未來要做什麼** → Spec
- 講**現在怎麼運作** → Technical Handbook
- 講**為什麼這樣決定、放棄了什麼** → ADR
- 講**怎麼操作** → Operator Guide（工作人員）或 Participant Guide（玩家）

同一段話只能有一個家。要在別處提到它，**放連結，不要複製**——複製就是下一次 drift 的來源。

## 索引

### Guides

- [Participant Guide](./participant-guide/README.md) — 入場、Portal、SOC、Flag/Hint/Score、
  Battleboard、常見問題、Rules of Engagement
- [Operator Guide](./operator-guide/README.md) — pre-flight、部署、建場次、座位與邀請、
  監控、故障處理、重置、收尾
- [Technical Handbook](./technical-handbook/README.md) — 架構、網段、runtime、身分授權、
  遙測、Range Core、評估引擎、部署、安全邊界、開發流程

### Architecture

- [Role × UI × Permission Matrix](./architecture/role-ui-permission-matrix.md) —
  五類角色 × 七個 UI × 能看/能做/不能知道/由什麼強制
- [ADR 0001](./adr/0001-p1-output-contract.md) — P1 對外契約
- [ADR 0002](./adr/0002-ws5-objective-scoring.md) — WS5 Objective 與計分
- [ADR 0003](./adr/0003-ws8-range-core-admission.md) — WS8 Range Core ＋ Admission
- [ADR 0004](./adr/0004-roster-attribution-via-trusted-gateway.md) — 名冊歸屬改由可信 gateway
- [p1-output-contract.md](./p1-output-contract.md) — P1 定版對外契約

### Agents

- [issue-tracker.md](./agents/issue-tracker.md) ／ [triage-labels.md](./agents/triage-labels.md)
  ／ [domain.md](./agents/domain.md)

### repo 根目錄

- [`README.md`](../README.md) — Cyber 是什麼 ＋ Quick Start ＋ 文件入口
- [`資安攻防平台_系統架構設計文件_v0.1.md`](../資安攻防平台_系統架構設計文件_v0.1.md) —
  系統架構設計（SA），**單一真相來源**
- [`purple_platform_plan.md`](../purple_platform_plan.md) — 紫隊工作規劃、計分模型、缺口分類
- [`ui/README.md`](../ui/README.md) — UI 實作導覽、完成度對照、已知缺口
