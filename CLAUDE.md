# cyber — 資安攻防平台 Agent Guide

目錄層級的 agent 設定，範圍限於 `cyber/`。workspace 層級的設定在 root [CLAUDE.md](../CLAUDE.md)。

## Agent skills

### Issue tracker

Issue 在 **GitHub Issues**（`Graylee0128/cyber`，`gh` CLI 操作）。見 `docs/agents/issue-tracker.md`。

資料夾約定：

| 東西 | 位置 |
|---|---|
| ticket | GitHub Issues |
| 活契約 | `docs/` |
| 進行中的 map／草稿 | `.scratch/<feature>/` |
| 做完的 | `archive/` |

### Triage labels

預設五個標準角色，標籤名＝角色名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context（`cyber/CONTEXT.md` + `cyber/docs/adr/`，由 `/domain-modeling` 日後 lazy 建立）。見 `docs/agents/domain.md`。
