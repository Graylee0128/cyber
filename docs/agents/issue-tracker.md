# Issue tracker: GitHub Issues

Issues for the `cyber/` scope live in **GitHub Issues** on `Graylee0128/cyber`, driven with the `gh` CLI.

遷移自本地 markdown（2026-08-08）。`.scratch/<feature>/issues/` 已刪除；舊內容在 git 歷史裡。

This is a directory-scoped tracker. It is separate from the workspace-level tracker at the repo root (`docs/agents/issue-tracker.md`) — work on the 資安攻防平台 / Cyber Range product belongs here.

## 仍留在檔案裡的東西

Issue 搬走了，**spec 與 map 沒有**：

| 東西 | 位置 | 為什麼不搬 |
|---|---|---|
| Spec | `.scratch/<feature>/spec.md` | 契約要能被 diff、被 PR review、跟程式碼一起版控 |
| Map | `.scratch/<feature>/map.md` | 依賴圖與 Decisions-so-far 是一份敘事，拆成 issue 會散掉 |
| ADR | `docs/adr/` | 同上 |

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
