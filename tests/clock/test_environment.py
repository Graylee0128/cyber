"""對真實環境的斷言 —— 不是 red-green-refactor（見 map.md 測試性質分級）。

這裡跑的是真的 `config/clock-nodes.yaml`，會實際去讀每個節點的時間。
"""

import pytest


@pytest.mark.environment
def test_all_configured_nodes_are_in_sync(clock_in_sync):
    report = clock_in_sync
    assert report.ok, report.summary()


@pytest.mark.environment
def test_check_actually_measured_something(clock_in_sync):
    """防止設定檔被清空後檢查變成空跑而恆綠。"""
    assert len(clock_in_sync.results) >= 1


@pytest.mark.environment
def test_every_node_reports_a_measured_skew(clock_in_sync):
    """每個節點都要有數字，不能只回報「通過」。"""
    for result in clock_in_sync.results:
        assert result.skew_ms is not None, result.detail
