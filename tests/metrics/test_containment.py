"""Containment duration —— 純函數，不觸 PG（票 05）。"""

from datetime import timedelta

from purple.metrics.containment import containment_duration

import pytest


def _event(lifecycle: str, observed_at: str) -> dict:
    return {"event_id": "evt-1", "lifecycle": lifecycle, "observed_at": observed_at}


def test_firing_and_resolved_gives_the_difference():
    events = [
        _event("firing", "2026-08-08T14:30:00+08:00"),
        _event("resolved", "2026-08-08T14:35:00+08:00"),
    ]
    assert containment_duration(events) == timedelta(minutes=5)


def test_order_does_not_matter():
    events = [
        _event("resolved", "2026-08-08T14:35:00+08:00"),
        _event("firing", "2026-08-08T14:30:00+08:00"),
    ]
    assert containment_duration(events) == timedelta(minutes=5)


def test_only_firing_returns_none():
    """攻擊還沒停，duration 尚不存在 —— 不可用 0 硬湊。"""
    assert containment_duration([_event("firing", "2026-08-08T14:30:00+08:00")]) is None


def test_naive_timestamp_is_rejected():
    events = [
        _event("firing", "2026-08-08T14:30:00"),
        _event("resolved", "2026-08-08T14:35:00"),
    ]
    with pytest.raises(ValueError, match="無時區"):
        containment_duration(events)
