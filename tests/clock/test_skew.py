"""Clock skew 判定的純函數層 —— 真 TDD，不需要 docker，秒級。

票 01 的核心：時間不同步，紅隊動作與藍隊偵測關聯不起來，
MTTD 與 action coverage 全部失去意義。
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.clock.skew import (
    MAX_SKEW_MS,
    NodeReading,
    Verdict,
    evaluate,
)

REFERENCE = datetime(2026, 8, 8, 14, 30, 0, tzinfo=timezone.utc)


def reading(node: str, offset_ms: float) -> NodeReading:
    return NodeReading(
        node=node,
        node_time=REFERENCE + timedelta(milliseconds=offset_ms),
        reference_time=REFERENCE,
    )


def unreachable(node: str, error: str = "connection refused") -> NodeReading:
    return NodeReading(node=node, node_time=None, reference_time=REFERENCE, error=error)


class TestVerdict:
    def test_node_within_threshold_is_in_sync(self):
        report = evaluate([reading("alloy", 12)])
        assert report.results[0].verdict is Verdict.IN_SYNC
        assert report.ok

    def test_node_beyond_threshold_is_skewed(self):
        report = evaluate([reading("alloy", MAX_SKEW_MS + 1)])
        assert report.results[0].verdict is Verdict.SKEWED
        assert not report.ok

    def test_threshold_boundary_is_inclusive(self):
        """恰好等於門檻算通過 —— 邊界行為明寫，不留給實作者猜。"""
        report = evaluate([reading("alloy", MAX_SKEW_MS)])
        assert report.results[0].verdict is Verdict.IN_SYNC

    def test_node_behind_reference_uses_absolute_skew(self):
        """落後與超前一樣有害，用絕對值判定。"""
        report = evaluate([reading("falco", -(MAX_SKEW_MS + 50))])
        assert report.results[0].verdict is Verdict.SKEWED
        assert report.results[0].skew_ms == pytest.approx(-(MAX_SKEW_MS + 50))


class TestNeverSilentlyGreen:
    """這一組測試的存在理由：一個從不會紅的檢查，比沒有檢查更糟。"""

    def test_unreachable_node_is_not_in_sync(self):
        report = evaluate([unreachable("falco")])
        assert report.results[0].verdict is Verdict.UNREACHABLE
        assert not report.ok

    def test_unreachable_node_keeps_its_error(self):
        report = evaluate([unreachable("falco", error="docker: no such container")])
        assert "no such container" in report.results[0].detail

    def test_empty_node_list_is_not_ok(self):
        """沒有節點不等於全部同步 —— 設定檔寫錯不該渲染成綠燈。"""
        report = evaluate([])
        assert not report.ok
        assert "no nodes" in report.summary().lower()

    def test_naive_datetime_is_rejected(self):
        """無時區的時間戳是本票要消滅的東西，不可默默當成 UTC。"""
        naive = NodeReading(
            node="alloy",
            node_time=datetime(2026, 8, 8, 14, 30, 0),
            reference_time=REFERENCE,
        )
        with pytest.raises(ValueError, match="timezone"):
            evaluate([naive])


class TestMeasurementUncertainty:
    """`docker exec date` 本身要花時間。往返時間若與門檻同量級，
    量到的偏差是雜訊，不能當成同步的證據。"""

    def test_slow_round_trip_makes_the_reading_uncertain(self):
        r = NodeReading(
            node="alloy",
            node_time=REFERENCE,
            reference_time=REFERENCE,
            round_trip_ms=400,  # ±200ms 不確定性 > 100ms 門檻
        )
        report = evaluate([r])
        assert report.results[0].verdict is Verdict.UNCERTAIN
        assert not report.ok

    def test_uncertain_reading_says_why(self):
        r = NodeReading(
            node="alloy", node_time=REFERENCE, reference_time=REFERENCE, round_trip_ms=400
        )
        detail = evaluate([r]).results[0].detail
        assert "round trip" in detail
        assert "400" in detail

    def test_fast_round_trip_still_judged_normally(self):
        r = NodeReading(
            node="alloy", node_time=REFERENCE, reference_time=REFERENCE, round_trip_ms=8
        )
        assert evaluate([r]).results[0].verdict is Verdict.IN_SYNC

    def test_uncertainty_beats_skew_in_reporting(self):
        """量測不可信時，不可回報一個看似精確的偏差值誤導人。"""
        r = NodeReading(
            node="alloy",
            node_time=REFERENCE + timedelta(milliseconds=500),
            reference_time=REFERENCE,
            round_trip_ms=400,
        )
        assert evaluate([r]).results[0].verdict is Verdict.UNCERTAIN


class TestNamesTheFailingNode:
    """驗收條件：檢查失敗時明確指出是哪個節點，而非只回報「失敗」。"""

    def test_failing_nodes_are_listed(self):
        report = evaluate(
            [reading("alloy", 5), reading("falco", 900), unreachable("loki")]
        )
        assert {r.node for r in report.failing} == {"falco", "loki"}

    def test_summary_names_each_failing_node(self):
        report = evaluate([reading("alloy", 5), reading("falco", 900)])
        summary = report.summary()
        assert "falco" in summary
        assert "900" in summary

    def test_summary_does_not_hide_healthy_count(self):
        report = evaluate([reading("alloy", 5), reading("falco", 900)])
        assert "1/2" in report.summary()


class TestThresholdIsConfigurable:
    """驗收條件：偏差門檻寫成具名常數，日後可調。"""

    def test_default_threshold_is_the_named_constant(self):
        assert MAX_SKEW_MS == 100

    def test_caller_may_tighten_the_threshold(self):
        readings = [reading("alloy", 50)]
        assert evaluate(readings).ok
        assert not evaluate(readings, threshold_ms=10).ok

    def test_report_carries_the_threshold_it_used(self):
        report = evaluate([reading("alloy", 5)], threshold_ms=10)
        assert report.threshold_ms == 10
