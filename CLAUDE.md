# cyber — 資安攻防平台 Agent Guide

目錄層級的 agent 設定，範圍限於 `cyber/`。workspace 層級的設定在 root [CLAUDE.md](../CLAUDE.md)。

## Agent skills

### Issue tracker

Issue 在 **GitHub Issues**（`Graylee0128/cyber`，`gh` CLI 操作）。**spec 與 map 仍留在 `.scratch/<feature>/`** —— 契約要跟程式碼一起版控。見 `docs/agents/issue-tracker.md`。

### Triage labels

預設五個標準角色，標籤名＝角色名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context（`cyber/CONTEXT.md` + `cyber/docs/adr/`，由 `/domain-modeling` 日後 lazy 建立）。見 `docs/agents/domain.md`。
