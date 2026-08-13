# cyber — 資安攻防平台 Agent Guide

目錄層級的 agent 設定，範圍限於 `cyber/`。workspace 層級的設定在 root [CLAUDE.md](../CLAUDE.md)。

## Agent skills

### Issue tracker

Issue 在 **GitHub Issues**（`Graylee0128/cyber`，`gh` CLI 操作）。見 `docs/agents/issue-tracker.md`。

實作流程以 [#67](https://github.com/Graylee0128/cyber/issues/67) 為基準，但 **2026-08-13 起放寬**：
Codex 仍是主要的 implementation owner，**Claude 不再受限於只做 review**——
review 找到的問題可以直接動手改、直接 push、直接 merge，不必回交 Codex 再等一輪。
review 的職責（核對 SA／ADR／spec、phase 邊界、跨 Workstream 契約）不變，只是修正路徑變短了。

只替仍 open 的 canonical work package 開 branch／PR；duplicate issue 不得成為 implementation target。

`ready-for-agent` 只表示規格完整。真正可開工還要確認 issue body 的 **Authoritative blockers**
全部解除。每個 canonical work package 原則上只開一個主要 draft PR；可用分段 commit 交付，
但不得在同一 branch／PR 混入另一個 canonical scope。

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
