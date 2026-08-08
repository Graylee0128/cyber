"""取樣編排 —— 用假 probe 測，不需要 docker。

真正碰 subprocess 的只有 `probes.read_docker`，它由環境斷言測試覆蓋。
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.clock.config import NodeConfig
from purple.clock.probes import ProbeError
from purple.clock.runner import measure, rebase_to_reference
from purple.clock.skew import NodeReading, Verdict, evaluate

T0 = datetime(2026, 8, 8, 14, 30, 0, tzinfo=timezone.utc)


class FakeHostClock:
    """依序吐出預定的本機時刻，模擬取樣前後的兩次讀秒。"""

    def __init__(self, *moments: datetime):
        self._moments = list(moments)

    def __call__(self) -> datetime:
        return self._moments.pop(0)


class TestMeasure:
    def test_reference_time_is_the_midpoint_of_the_round_trip(self):
        """取樣要花時間。用區間中點當基準，是對稱誤差下最好的估計。"""
        host = FakeHostClock(T0, T0 + timedelta(milliseconds=40))
        r = measure(
            NodeConfig(name="alloy", probe="local"),
            read=lambda: T0 + timedelta(milliseconds=20),
            host_clock=host,
        )
        assert r.reference_time == T0 + timedelta(milliseconds=20)
        assert r.round_trip_ms == pytest.approx(40)
        assert evaluate([r]).results[0].verdict is Verdict.IN_SYNC

    def test_probe_failure_becomes_an_unreachable_reading(self):
        host = FakeHostClock(T0, T0 + timedelta(milliseconds=5))
        r = measure(
            NodeConfig(name="falco", probe="docker", container="falco"),
            read=_raises(ProbeError("no such container: falco")),
            host_clock=host,
        )
        assert r.node_time is None
        assert "no such container" in (r.error or "")
        assert evaluate([r]).results[0].verdict is Verdict.UNREACHABLE

    def test_probe_failure_does_not_escape(self):
        """單一節點掛掉不該中斷整批檢查 —— 否則只會看到第一個錯誤。"""
        host = FakeHostClock(T0, T0)
        measure(
            NodeConfig(name="falco", probe="local"),
            read=_raises(ProbeError("boom")),
            host_clock=host,
        )  # 不拋例外即通過


class TestRebaseToReference:
    """所有節點都先對本機取樣，再換算成相對於基準節點的偏差。
    基準節點自己若偏了，不該讓其他節點跟著被誤判。"""

    def test_reference_node_has_zero_skew_by_definition(self):
        readings = [_reading("mgmt-host", 300), _reading("alloy", 320)]
        rebased = rebase_to_reference(readings, reference="mgmt-host")
        results = {r.node: r for r in evaluate(rebased).results}
        assert results["mgmt-host"].skew_ms == pytest.approx(0)

    def test_other_nodes_are_measured_against_the_reference_not_the_host(self):
        """本機整體慢 300ms，但兩個節點彼此只差 20ms —— 應判為同步。"""
        readings = [_reading("mgmt-host", 300), _reading("alloy", 320)]
        report = evaluate(rebase_to_reference(readings, reference="mgmt-host"))
        assert report.ok
        assert report.results[1].skew_ms == pytest.approx(20)

    def test_unreachable_reference_fails_every_node(self):
        """基準讀不到，就沒有任何節點的偏差是可信的，不可只標基準失敗。"""
        readings = [
            NodeReading(node="mgmt-host", node_time=None, reference_time=T0, error="down"),
            _reading("alloy", 5),
        ]
        report = evaluate(rebase_to_reference(readings, reference="mgmt-host"))
        assert not report.ok
        assert {r.verdict for r in report.results} == {Verdict.UNREACHABLE}
        assert "reference" in report.results[1].detail

    def test_unknown_reference_name_is_an_error(self):
        with pytest.raises(ValueError, match="nowhere"):
            rebase_to_reference([_reading("alloy", 5)], reference="nowhere")


def _reading(node: str, offset_ms: float) -> NodeReading:
    """節點時間比本機快 offset_ms。"""
    return NodeReading(
        node=node,
        node_time=T0 + timedelta(milliseconds=offset_ms),
        reference_time=T0,
        round_trip_ms=4,
    )


def _raises(exc: Exception):
    def _read():
        raise exc

    return _read
