"""P2 Latency：MTTD／MTTR／containment duration —— 三個終點不得串位（#21、ADR ⑦）。

    MTTD                executed_at        → firing_at            偵測有多快
    MTTR                firing_at          → response_executed_at 反應有多快
    containment duration firing_at         → resolved_at          攻擊多久停止

`containment duration` **不是** MTTR：攻擊自行停止時 containment 有值，而 MTTR
不可得——那時沒有任何 response 生效過，硬填一個數字等於宣稱藍隊做了沒做的事。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

#: #21 要求的量測筆數。少於這個數量的樣本不得當成最終 p50/p95 交付。
REQUIRED_SAMPLE_COUNT = 20

#: Grafana 的 evaluation interval 讓 MTTD 有一個結構性下限，
#: 報告必須標註，否則讀者會把工具的取樣週期誤讀成偵測能力。
MTTD_FLOOR_NOTE = "Grafana eval interval creates an approximately 10s floor"

#: 演練模式的 MTTR 含人的決策時間（#48 之後封鎖由藍隊觸發）。
#: 與自動模式混在同一個分佈裡比較，會把「人在迴圈」誤讀成效能退步。
MTTR_MODE_NOTE = "includes human decision time"


@dataclass(frozen=True)
class LatencyRun:
    """一次動作的四個時間點。缺席的終點一律 None，不以 0 或 now 頂替。"""

    action_id: str
    mode: str
    executed_at: datetime
    firing_at: datetime | None = None
    response_executed_at: datetime | None = None
    resolved_at: datetime | None = None

    @property
    def mttd(self) -> timedelta | None:
        if self.firing_at is None:
            return None
        return self.firing_at - self.executed_at

    @property
    def mttr(self) -> timedelta | None:
        if self.firing_at is None or self.response_executed_at is None:
            return None
        return self.response_executed_at - self.firing_at

    @property
    def containment_duration(self) -> timedelta | None:
        if self.firing_at is None or self.resolved_at is None:
            return None
        return self.resolved_at - self.firing_at


@dataclass(frozen=True)
class LatencySummary:
    """單一模式的分佈摘要。模式之間不合併——見 MTTR_MODE_NOTE。"""

    mode: str
    sample_count: int
    mttd_p50_ms: int | None
    mttd_p95_ms: int | None
    mttr_p50_ms: int | None
    mttr_p95_ms: int | None
    containment_p50_ms: int | None
    containment_p95_ms: int | None
    mttd_floor_note: str = MTTD_FLOOR_NOTE
    mttr_mode_note: str = MTTR_MODE_NOTE


def summarize_latency(runs: list[LatencyRun]) -> dict[str, LatencySummary]:
    """依 mode 分組計算 p50/p95。每組都必須恰好有 REQUIRED_SAMPLE_COUNT 筆。"""
    by_mode: dict[str, list[LatencyRun]] = {}
    for item in runs:
        by_mode.setdefault(item.mode, []).append(item)

    summaries: dict[str, LatencySummary] = {}
    for mode, items in by_mode.items():
        if len(items) != REQUIRED_SAMPLE_COUNT:
            raise ValueError(
                f"mode {mode!r} has {len(items)} runs; "
                f"a final latency measurement needs exactly {REQUIRED_SAMPLE_COUNT}"
            )
        summaries[mode] = LatencySummary(
            mode=mode,
            sample_count=len(items),
            mttd_p50_ms=_percentile_ms([i.mttd for i in items], 50),
            mttd_p95_ms=_percentile_ms([i.mttd for i in items], 95),
            mttr_p50_ms=_percentile_ms([i.mttr for i in items], 50),
            mttr_p95_ms=_percentile_ms([i.mttr for i in items], 95),
            containment_p50_ms=_percentile_ms([i.containment_duration for i in items], 50),
            containment_p95_ms=_percentile_ms([i.containment_duration for i in items], 95),
        )
    return summaries


def _percentile_ms(values: list[timedelta | None], percentile: int) -> int | None:
    """不可得的樣本不參與計算——把 None 當 0 會讓分佈往下漂。

    p50 取中位數；尾端百分位取 nearest-rank（實際觀測到的樣本值），
    不做線性內插——內插會回報一個從未真正發生過的延遲數字。
    """
    present = sorted(v.total_seconds() * 1000 for v in values if v is not None)
    if not present:
        return None
    if percentile == 50:
        return round(statistics.median(present))
    rank = math.ceil((percentile / 100) * len(present))
    return round(present[min(max(rank, 1), len(present)) - 1])
