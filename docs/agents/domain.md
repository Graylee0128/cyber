# Domain Docs

How the engineering skills should consume the `cyber/` scope's domain documentation when exploring it.

This is a directory-scoped config. "Root" below means `cyber/`, not the workspace root.

## Before exploring, read these

- **`cyber/CONTEXT.md`** — the scope's glossary and domain model.
- **`cyber/docs/adr/`** — read ADRs that touch the area you're about to work in.
- **`cyber/資安攻防平台_系統架構設計文件_v0.1.md`** — the current system architecture design doc; the SVG diagrams alongside it are its rendered views.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context (this scope):

```
cyber/
├── CONTEXT.md
├── docs/
│   ├── adr/
│   │   ├── 0001-....md
│   │   └── 0002-....md
│   └── agents/
└── 資安攻防平台_系統架構設計文件_v0.1.md
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

This matters here: the architecture doc already fixes vocabulary for Red / Blue / Purple / Instructor roles, Cyber Range Core, Game Event Layer, Detection & Telemetry, and Exercise State. Reuse those terms rather than inventing parallel names.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
