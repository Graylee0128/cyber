"""raw log 保留時段（票 11、ADR ⑩）—— 純函數。

演練期間才留「沒觸發任何規則」的原始 log；時段外不留。沒有 raw，就無法證明
「有 log 但沒規則」（偵測缺口），所以這個窗是缺口分類的前提。

開窗＝演練開始前 10 分鐘，關窗＝結束後 30 分鐘。自動切換，不需人工。
"""

from __future__ import annotations

from collections.abc import Iterable
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


def raw_coverage_windows_for(
    retention: RetentionWindow, event_timestamps: Iterable[datetime]
) -> tuple[str, ...]:
    """把報告引用到的事件時間，依保留窗自動分類成人類可讀的區段字串
    （票 #98 項目 3，§4.2）——取代原本「呼叫端手填字串」的做法，過期判定
    由 `RetentionWindow.retains` 算，不是報告產出時憑印象填。

    連續同狀態（有 raw／已過期）的事件合併成一個範圍，不會退化成每個
    事件各佔一行的雜訊。**只看「raw 本身還在不在」**——已經被
    `retention/report.py` 的 `snapshot_report` 嵌入報告本身的證據不受
    這裡影響：那些即使 raw 過期，報告內的副本仍可讀，這裡回答的是另一個
    問題，兩者不重疊，不重造快照機制。
    """
    ordered = sorted(event_timestamps)
    if not ordered:
        return ()

    def _label(retained: bool) -> str:
        return "有 raw" if retained else "raw 已過期"

    def _render(start: datetime, end: datetime, retained: bool) -> str:
        if start == end:
            return f"{start:%H:%M} {_label(retained)}"
        return f"{start:%H:%M}–{end:%H:%M} {_label(retained)}"

    windows: list[str] = []
    group_start = group_end = ordered[0]
    group_retained = retention.retains(ordered[0])

    for ts in ordered[1:]:
        retained = retention.retains(ts)
        if retained == group_retained:
            group_end = ts
        else:
            windows.append(_render(group_start, group_end, group_retained))
            group_start = group_end = ts
            group_retained = retained
    windows.append(_render(group_start, group_end, group_retained))
    return tuple(windows)
