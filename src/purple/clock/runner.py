"""取樣編排 —— 把 probe 的結果整理成可判定的 NodeReading。

`measure` 收斂所有 I/O 失敗，`rebase_to_reference` 是純函數。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from purple.clock.config import ClockConfig, NodeConfig
from purple.clock.probes import ProbeError, probe_for, read_local
from purple.clock.skew import NodeReading, SkewReport, evaluate


def measure(
    node: NodeConfig,
    read: Callable[[], datetime],
    host_clock: Callable[[], datetime] = read_local,
) -> NodeReading:
    """對單一節點取樣一次。任何 probe 失敗都轉成 unreachable，不向外拋。"""
    before = host_clock()
    try:
        node_time = read()
    except ProbeError as exc:
        return NodeReading(
            node=node.name, node_time=None, reference_time=before, error=str(exc)
        )
    after = host_clock()

    round_trip = after - before
    return NodeReading(
        node=node.name,
        node_time=node_time,
        # 區間中點：對稱誤差下對「node_time 對應的本機時刻」最好的估計。
        reference_time=before + round_trip / 2,
        round_trip_ms=round_trip.total_seconds() * 1000,
    )


def rebase_to_reference(
    readings: list[NodeReading] | tuple[NodeReading, ...], reference: str
) -> tuple[NodeReading, ...]:
    """把「相對本機」的取樣換算成「相對基準節點」。

    檢查主機自己的時鐘不必是真相 —— 真相是基準節點。
    """
    by_name = {r.node: r for r in readings}
    if reference not in by_name:
        raise ValueError(f"reference node {reference!r} was not measured")

    ref = by_name[reference]
    if ref.node_time is None:
        # 基準讀不到，沒有任何節點的偏差是可信的。全部標成 unreachable，
        # 而不是只讓基準一個人失敗、其餘照舊顯示綠燈。
        reason = f"reference node {reference!r} unreachable ({ref.error or 'unknown'})"
        return tuple(
            NodeReading(
                node=r.node,
                node_time=None,
                reference_time=r.reference_time,
                error=r.error if r.node == reference else reason,
            )
            for r in readings
        )

    ref_offset = ref.node_time - ref.reference_time
    return tuple(
        r
        if r.node_time is None
        else NodeReading(
            node=r.node,
            node_time=r.node_time,
            reference_time=r.reference_time + ref_offset,
            round_trip_ms=r.round_trip_ms,
        )
        for r in readings
    )


def check(config: ClockConfig) -> SkewReport:
    """跑完整檢查：逐節點取樣 → 換算到基準 → 判定。"""
    readings = [measure(node, probe_for(node)) for node in config.nodes]
    rebased = rebase_to_reference(readings, config.reference)
    return evaluate(rebased, threshold_ms=config.threshold_ms)
