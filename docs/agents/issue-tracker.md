# Issue tracker: GitHub Issues

Issues for the `cyber/` scope live in **GitHub Issues** on `Graylee0128/cyber`, driven with the `gh` CLI.

遷移自本地 markdown（2026-08-08）。`.scratch/<feature>/issues/` 已刪除；舊內容在 git 歷史裡。

This is a directory-scoped tracker. It is separate from the workspace-level tracker at the repo root (`docs/agents/issue-tracker.md`) — work on the 資安攻防平台 / Cyber Range product belongs here.

## 仍留在檔案裡的東西

Issue 搬走了，**spec 與 map 沒有**（契約與依賴敘事要能被 diff、被 PR review、跟程式碼一起版控）：

| 東西 | 位置 | 什麼時候搬 |
|---|---|---|
| Spec（活契約） | `docs/` | 一旦定版、開始被票消費，就從 `.scratch/` 遷出 |
| Map（進行中） | `.scratch/<feature>/` | 執行導覽在功能開發期間持續更新 |
| Map（做完） | `archive/` | 功能收工後遷入，存查用，不再更新 |
| ADR | `docs/adr/` | 一開始就在，不移動 |

範例：P1 的 spec 已定版遷至 [docs/p1-output-contract.md](../p1-output-contract.md)；
P1 已結，map 遷至 [archive/p1-output-contract-map.md](../../archive/p1-output-contract-map.md)；
P2 仍在進行，map 在 [.scratch/p2-evaluation/map.md](../../.scratch/p2-evaluation/map.md)；
WS1／WS5 的 spec 已定案但尚未被票消費，仍在
[.scratch/ws1-game-design/spec.md](../../.scratch/ws1-game-design/spec.md) 與
[.scratch/ws5-range-core/spec.md](../../.scratch/ws5-range-core/spec.md)。

Map 以 issue 編號（`#N`）指向 GitHub，不再指向本地檔案。

## Conventions

- 一張票一個 issue，標題沿用 `NN — 標題` 形式
- Triage state 用 label（見 `triage-labels.md`）
- 依賴以內文的 `**Blocked by:** #N` 記錄 —— 本 repo 不使用 sub-issues
- Issue 內文的檔案連結要用**絕對 URL**（`https://github.com/Graylee0128/cyber/blob/master/...`），相對路徑在 issue 頁面會壞掉

## When a skill says "publish to the issue tracker"

```bash
gh issue create --title "<NN> — <標題>" --body-file <file> --label ready-for-agent
```

依賴順序建立（blocker 先），這樣後建的才引用得到真實編號。

## When a skill says "fetch the relevant ticket"

```bash
gh issue view <number> --json title,body,labels,comments
```

## Frontier

```bash
gh issue list --label ready-for-agent --state open
```

再逐一看內文的 `Blocked by`，取所有 blocker 都已 closed 的最小編號。

## 完成一張票

```bash
gh issue close <number> --reason completed --comment "由 commit <sha> 完成，<驗證證據>"
```

留言要帶**證據**（測試數、CI 狀態、commit sha），不是「done」。未驗證的部分明寫，並指出由哪張票接手。
