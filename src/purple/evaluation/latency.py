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
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

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


# ── 從真實儲存的事件組出 LatencyRun（#90 Phase 4）──────────────────────────────
#
# 四個時間點各有各的真相來源，串位就是把「偵測很快」讀成「反應很慢」：
#
#   executed_at            action_executions（紅隊執行 ground truth）
#   firing_at              該動作的 firing 偵測事件 observed_at
#   response_executed_at   回應那筆攻擊的 response.executed observed_at
#   resolved_at            與 firing 同 event_id 的 resolved 事件 observed_at
#
# 關聯一律走 event_id / action_id，**不靠時間窗鄰近性猜**（與 evaluator 同契約）。


def _parse_ts(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"observed_at must be an ISO-8601 string, got {type(value).__name__}")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"observed_at {value!r} has no timezone")
    return parsed


def build_latency_run(
    action_id: str,
    mode: str,
    executed_at: datetime,
    *,
    firing: Mapping[str, Any] | None = None,
    response: Mapping[str, Any] | None = None,
    resolution: Mapping[str, Any] | None = None,
) -> LatencyRun:
    """把一個動作的四個真實事件組成一次量測（純函數）。

    缺席的終點一律 None，不以 0 或 now 頂替：攻擊自行停止而沒有 response 時，
    `response` 是 None → MTTR 不可得，但 `resolution` 仍可給 containment 一個值。
    這正是 #21 「containment 有值、MTTR 不可得」那條驗收的落點。
    """
    return LatencyRun(
        action_id=action_id,
        mode=mode,
        executed_at=executed_at,
        firing_at=_parse_ts(firing["observed_at"]) if firing else None,
        response_executed_at=_parse_ts(response["observed_at"]) if response else None,
        resolved_at=_parse_ts(resolution["observed_at"]) if resolution else None,
    )


@dataclass(frozen=True)
class LatencyAssembler:
    """讀真實儲存，把一場演練的完成週期組成 `LatencyRun` 清單（#90 Phase 4 的 #44 銜接點）。

    依賴以 duck-typing 注入：

    - `executions`：需有 `for_exercise(exercise_id) -> {action_id: ActionExecution}`
    - `events`：`CoreEventStore`，需有 `firings_by_action` / `responses_by_attack_event`
      / `resolutions_by_event`
    - `mode_of`：`action_id -> "exercise" | "automatic"`。mode 來自回應的觸發方式
      （ResponseCommand.triggered_by），**不由這裡猜**；填法屬 #44/#51 整合，預設整場
      單一 mode。

    `build` 只組不算：要不要 20 筆、p50/p95 交給 `summarize_latency`，一個算一個存。
    """

    executions: Any
    events: Any
    mode_of: Callable[[str], str]

    def build(self, exercise_id: str) -> list[LatencyRun]:
        executed = self.executions.for_exercise(exercise_id)
        firings = self.events.firings_by_action(exercise_id)
        responses = self.events.responses_by_attack_event(exercise_id)
        resolutions = self.events.resolutions_by_event(exercise_id)

        runs: list[LatencyRun] = []
        for action_id, execution in executed.items():
            firing = firings.get(action_id)
            # response 與 resolution 都掛在 firing 的 event_id 上：沒有 firing 就沒有
            # 可對接的 join key，硬去撈會把別的動作的回應算到這個頭上。
            firing_event_id = firing["event_id"] if firing else None
            runs.append(
                build_latency_run(
                    action_id,
                    self.mode_of(action_id),
                    execution.executed_at,
                    firing=firing,
                    response=responses.get(firing_event_id) if firing_event_id else None,
                    resolution=resolutions.get(firing_event_id) if firing_event_id else None,
                )
            )
        return runs
