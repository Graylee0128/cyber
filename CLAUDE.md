# cyber — 資安攻防平台 Agent Guide

目錄層級的 agent 設定，範圍限於 `cyber/`。workspace 層級的設定在 root [CLAUDE.md](../CLAUDE.md)。

## Agent skills

### Issue tracker

Issue 與 spec 以 markdown 檔存於 `cyber/.scratch/<feature>/`（本地 markdown，不進 GitHub Issues）。見 `docs/agents/issue-tracker.md`。

### Triage labels

預設五個標準角色，標籤名＝角色名（`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context（`cyber/CONTEXT.md` + `cyber/docs/adr/`，由 `/domain-modeling` 日後 lazy 建立）。見 `docs/agents/domain.md`。
