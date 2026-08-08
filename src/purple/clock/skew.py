"""Clock skew 判定 —— 純函數，無 I/O。

實際去讀各節點時間的是 `probes.py`（shell 層）。這裡只做判定，
所以判定邏輯可以在毫秒內測完，不需要起 docker。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

#: 偏差門檻。超過此值，紅隊動作與藍隊偵測的時序關聯不再可信。
MAX_SKEW_MS: float = 100.0


class Verdict(StrEnum):
    IN_SYNC = "in_sync"
    SKEWED = "skewed"
    #: 讀不到節點時間。刻意與 IN_SYNC 分開 —— 讀不到不是通過。
    UNREACHABLE = "unreachable"
    #: 取樣往返時間與門檻同量級，量到的偏差是雜訊。不可當成同步的證據。
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class NodeReading:
    """一次對單一節點的時間取樣。`node_time is None` 代表讀取失敗。

    `round_trip_ms` 是取樣本身花掉的時間。它決定這次取樣的不確定度為
    ±round_trip/2 —— 不確定度大過門檻時，這次取樣不能用來證明同步。
    """

    node: str
    node_time: datetime | None
    reference_time: datetime
    error: str | None = None
    round_trip_ms: float | None = None


@dataclass(frozen=True)
class NodeResult:
    node: str
    verdict: Verdict
    skew_ms: float | None
    detail: str

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.IN_SYNC


@dataclass(frozen=True)
class SkewReport:
    results: tuple[NodeResult, ...]
    threshold_ms: float

    @property
    def failing(self) -> tuple[NodeResult, ...]:
        return tuple(r for r in self.results if not r.ok)

    @property
    def ok(self) -> bool:
        # 空清單不算通過：設定檔寫錯而掃不到任何節點，是失敗而非全體同步。
        return bool(self.results) and not self.failing

    def summary(self) -> str:
        if not self.results:
            return "clock check FAILED: no nodes checked (設定檔沒有可檢查的節點)"

        healthy = len(self.results) - len(self.failing)
        head = f"clock check {'OK' if self.ok else 'FAILED'}: " \
               f"{healthy}/{len(self.results)} nodes within {self.threshold_ms:g}ms"
        if self.ok:
            return head
        lines = [head] + [f"  - {r.node}: {r.detail}" for r in self.failing]
        return "\n".join(lines)


def _require_aware(moment: datetime, node: str, label: str) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{node}: {label} 缺少 timezone。無時區的時間戳正是本檢查要消滅的東西，"
            f"不可默默當成 UTC。"
        )


def evaluate(
    readings: list[NodeReading] | tuple[NodeReading, ...],
    threshold_ms: float = MAX_SKEW_MS,
) -> SkewReport:
    """把一批取樣判定成報告。偏差以絕對值比較 —— 落後與超前一樣有害。"""
    results: list[NodeResult] = []

    for r in readings:
        if r.node_time is None:
            results.append(
                NodeResult(
                    node=r.node,
                    verdict=Verdict.UNREACHABLE,
                    skew_ms=None,
                    detail=f"unreachable ({r.error or 'no reason given'})",
                )
            )
            continue

        _require_aware(r.node_time, r.node, "node_time")
        _require_aware(r.reference_time, r.node, "reference_time")

        skew_ms = (r.node_time - r.reference_time).total_seconds() * 1000

        # 不確定度先判。量測不可信時回報一個看似精確的偏差值只會誤導人。
        if r.round_trip_ms is not None and r.round_trip_ms / 2 > threshold_ms:
            results.append(
                NodeResult(
                    node=r.node,
                    verdict=Verdict.UNCERTAIN,
                    skew_ms=None,
                    detail=(
                        f"measurement unusable: round trip {r.round_trip_ms:.0f}ms "
                        f"(±{r.round_trip_ms / 2:.0f}ms) 超過門檻 {threshold_ms:g}ms"
                    ),
                )
            )
            continue

        within = abs(skew_ms) <= threshold_ms
        results.append(
            NodeResult(
                node=r.node,
                verdict=Verdict.IN_SYNC if within else Verdict.SKEWED,
                skew_ms=skew_ms,
                detail=f"skew {skew_ms:+.0f}ms (threshold {threshold_ms:g}ms)",
            )
        )

    return SkewReport(results=tuple(results), threshold_ms=threshold_ms)
