# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in the `cyber/` scope's issue tracker.

The tracker is GitHub Issues. A label below is the actual GitHub label on an issue in `Graylee0128/cyber`.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Specification complete; authoritative blockers must still be checked |
| `ready-for-human`          | `ready-for-human`    | Requires a maintainer decision before implementation |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

`ready-for-agent` alone is not the execution frontier. Before opening a branch or PR, confirm the issue is an
open canonical work package and every **Authoritative blocker** in its body is resolved. Closed duplicates remain
historical evidence only and must not be implementation targets. The complete agent workflow baseline is
[#67](https://github.com/Graylee0128/cyber/issues/67).
