"""Exercise Report 的證據快照（票 11、ADR ⑩）—— 純函數。

retention 過期後原始 log 就沒了，但報告仍必須可讀。所以產出報告時，把引用到的
證據**快照進報告本身**：日後即使去查 raw 已經查不到，報告裡的證據還在。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class EvidenceExpired(Exception):
    """證據已過 retention，且報告當初沒有快照 —— 這正是快照要避免的情況。"""


@dataclass(frozen=True)
class Report:
    exercise_id: str
    #: 引用到的 event_id 清單（尚未快照時只有指標）。
    referenced_event_ids: tuple[str, ...]
    #: event_id → 已嵌入的證據。空 = 尚未快照。
    embedded: dict[str, Any] = field(default_factory=dict)

    @property
    def is_snapshotted(self) -> bool:
        return set(self.embedded) >= set(self.referenced_event_ids)

    def evidence_for(self, event_id: str) -> Any:
        """讀取證據。已快照就從報告內部取，永遠可讀。"""
        if event_id in self.embedded:
            return self.embedded[event_id]
        raise EvidenceExpired(
            f"{event_id} 的證據不在報告內，且 raw 可能已過 retention —— "
            f"報告產出時應已快照"
        )


def snapshot_report(report: Report, fetch_evidence: Callable[[str], Any]) -> Report:
    """把報告引用到的每筆證據抓下來、嵌進報告，回傳新的報告。

    產出報告的當下呼叫（此時 raw 還在）。之後 retention 過期，
    `evidence_for` 仍能從嵌入內容回傳，不再依賴 fetch。
    """
    embedded = dict(report.embedded)
    for event_id in report.referenced_event_ids:
        embedded[event_id] = fetch_evidence(event_id)
    return Report(
        exercise_id=report.exercise_id,
        referenced_event_ids=report.referenced_event_ids,
        embedded=embedded,
    )
