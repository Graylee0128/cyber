"""Containment duration —— 純函數（票 05、ADR ⑦）。

**這不是 MTTR。** containment duration 量的是「攻擊從被偵測到停止」有多久，
與藍隊做了什麼無關；MTTR 量的是「藍隊的 response 多久生效」（票 09）。
兩者終點不同，不可混用一個名字，否則報告會把兩件事說成一件。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable


def _observed_at(event: dict[str, Any]) -> datetime:
    moment = datetime.fromisoformat(event["observed_at"])
    if moment.tzinfo is None:
        raise ValueError(f"observed_at 無時區：{event['observed_at']!r}")
    return moment


def containment_duration(events_for_id: Iterable[dict[str, Any]]) -> timedelta | None:
    """給同一個 event_id 的所有 lifecycle 事件，回傳 firing→resolved 的時間差。

    尚未 resolved（只有 firing）時回 None —— 攻擊還沒停，duration 尚不存在，
    不可用 0 或「現在」硬湊一個數字。
    """
    by_lifecycle = {e["lifecycle"]: e for e in events_for_id}
    firing = by_lifecycle.get("firing")
    resolved = by_lifecycle.get("resolved")
    if firing is None or resolved is None:
        return None
    return _observed_at(resolved) - _observed_at(firing)
