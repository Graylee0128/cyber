"""CLI 的退出碼與輸出 —— 純函數層，不真的跑 subprocess。"""

from datetime import datetime, timedelta, timezone

from purple.clock.cli import EXIT_FAILED, EXIT_OK, render
from purple.clock.skew import NodeReading, evaluate

T0 = datetime(2026, 8, 8, 14, 30, 0, tzinfo=timezone.utc)


def _report(*offsets_ms: float):
    return evaluate(
        [
            NodeReading(
                node=f"node-{i}",
                node_time=T0 + timedelta(milliseconds=off),
                reference_time=T0,
            )
            for i, off in enumerate(offsets_ms)
        ]
    )


def test_in_sync_exits_zero():
    text, code = render(_report(5, 10))
    assert code == EXIT_OK
    assert "OK" in text


def test_skew_exits_nonzero():
    """CI 與後續測試的前置條件靠這個退出碼，不能只印訊息就回 0。"""
    _, code = render(_report(5, 900))
    assert code == EXIT_FAILED


def test_no_nodes_exits_nonzero():
    _, code = render(evaluate([]))
    assert code == EXIT_FAILED


def test_output_names_the_offending_node():
    text, _ = render(_report(5, 900))
    assert "node-1" in text
