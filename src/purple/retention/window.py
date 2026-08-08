"""raw log 保留時段（票 11、ADR ⑩）—— 純函數。

演練期間才留「沒觸發任何規則」的原始 log；時段外不留。沒有 raw，就無法證明
「有 log 但沒規則」（偵測缺口），所以這個窗是缺口分類的前提。

開窗＝演練開始前 10 分鐘，關窗＝結束後 30 分鐘。自動切換，不需人工。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: 演練開始「前」多久就開始保留 —— 攻擊前的基線也要看得到。
PRE_WINDOW_MINUTES = 10
#: 演練結束「後」多久才停止保留 —— 收尾動作與遲到的 log 也要涵蓋。
POST_WINDOW_MINUTES = 30


def _require_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None:
        raise ValueError(f"{label} 無時區：{moment!r}（保留時段的判定必須時區明確）")


@dataclass(frozen=True)
class RetentionWindow:
    exercise_start: datetime
    exercise_end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.exercise_start, "exercise_start")
        _require_aware(self.exercise_end, "exercise_end")
        if self.exercise_end < self.exercise_start:
            raise ValueError("exercise_end 早於 exercise_start")

    @property
    def opens_at(self) -> datetime:
        return self.exercise_start - timedelta(minutes=PRE_WINDOW_MINUTES)

    @property
    def closes_at(self) -> datetime:
        return self.exercise_end + timedelta(minutes=POST_WINDOW_MINUTES)

    def is_open(self, now: datetime) -> bool:
        """現在是否在保留窗內（自動，不需人工切換）。"""
        _require_aware(now, "now")
        return self.opens_at <= now <= self.closes_at

    def retains(self, timestamp: datetime) -> bool:
        """某個時間點的 raw log 是否落在保留範圍內。

        判斷的是「這筆 log 的時間戳在不在窗內」，與現在幾點無關 ——
        時段外的原始 log 一開始就不會被留下來。
        """
        _require_aware(timestamp, "timestamp")
        return self.opens_at <= timestamp <= self.closes_at
