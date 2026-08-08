"""Exercise Report 證據快照 —— 純函數（票 11）。

核心：快照後即使 retention 過期（fetch 會炸），報告仍可讀。
"""

from purple.retention.report import EvidenceExpired, Report, snapshot_report

import pytest

REPORT = Report(exercise_id="ex-001", referenced_event_ids=("evt-1", "evt-2"))


def _live_fetch(event_id: str):
    return {"event_id": event_id, "context": ["line-a", "line-b"]}


def _expired_fetch(event_id: str):
    raise RuntimeError("raw log 已過 retention，查不到")


class TestSnapshot:
    def test_snapshot_embeds_all_referenced_evidence(self):
        snap = snapshot_report(REPORT, _live_fetch)
        assert snap.is_snapshotted
        assert snap.evidence_for("evt-1")["context"] == ["line-a", "line-b"]

    def test_report_survives_retention_expiry(self):
        """產出時快照；之後 raw 沒了也不影響 —— 報告自帶證據。"""
        snap = snapshot_report(REPORT, _live_fetch)
        # 模擬 retention 過期：現在 fetch 會炸，但我們不再需要 fetch
        assert snap.evidence_for("evt-2")["event_id"] == "evt-2"

    def test_unsnapshotted_report_cannot_read_after_expiry(self):
        """沒快照的報告在過期後就讀不到 —— 這正是必須快照的理由。"""
        with pytest.raises(EvidenceExpired):
            REPORT.evidence_for("evt-1")

    def test_snapshot_does_not_mutate_the_original(self):
        snapshot_report(REPORT, _live_fetch)
        assert REPORT.embedded == {}  # 原報告不被就地改動
