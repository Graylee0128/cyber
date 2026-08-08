"""缺口分類 —— 純函數，真 TDD（票 08 的決定性邏輯）。

真實 Falco→Alloy→Loki 鏈路屬環境（workstream 6），本檔驗的是分類邏輯本身：
D1 對不對，就看這支分類器分不分得出偵測缺口與可見性缺口。
"""

from purple.metrics.gaps import MissClass, classify_miss, counts_towards_coverage
from purple.registry.source_registry import SourceState

import pytest


class TestTheDecisiveCase:
    def test_disabled_rule_shows_detection_gap_not_visibility_gap(self):
        """Falco 健康、syscall 有記錄、但規則被停用 → 偵測缺口，不是可見性缺口。

        這一條紅了代表我們實質退回 D3：Falco 覆蓋範圍內的偵測缺口全部不可觀測。
        """
        result = classify_miss(
            detected=False,               # 規則停用，沒告警
            source_state=SourceState.HEALTHY,  # Falco 活著
            telemetry_present=True,       # syscall 有進 Loki
        )
        assert result is MissClass.DETECTION_GAP
        assert result is not MissClass.VISIBILITY_GAP


class TestAllBranches:
    def test_detected_is_a_hit(self):
        assert classify_miss(
            detected=True, source_state=SourceState.HEALTHY, telemetry_present=True
        ) is MissClass.HIT

    def test_healthy_source_no_telemetry_is_visibility_gap(self):
        assert classify_miss(
            detected=False, source_state=SourceState.HEALTHY, telemetry_present=False
        ) is MissClass.VISIBILITY_GAP

    def test_stale_source_is_unknown_not_a_gap(self):
        """設備故障期間無法判定，不可算成藍隊漏了。"""
        assert classify_miss(
            detected=False, source_state=SourceState.STALE, telemetry_present=False
        ) is MissClass.UNKNOWN

    def test_absent_source_is_out_of_scope(self):
        assert classify_miss(
            detected=False, source_state=SourceState.ABSENT, telemetry_present=False
        ) is MissClass.OUT_OF_SCOPE

    def test_stale_beats_telemetry_flag(self):
        """來源 stale 時，telemetry_present 是什麼都無法判定 → 一律 unknown。"""
        assert classify_miss(
            detected=False, source_state=SourceState.STALE, telemetry_present=True
        ) is MissClass.UNKNOWN


class TestCoverageDenominator:
    def test_unknown_and_out_of_scope_excluded(self):
        assert not counts_towards_coverage(MissClass.UNKNOWN)
        assert not counts_towards_coverage(MissClass.OUT_OF_SCOPE)

    def test_hit_and_gaps_included(self):
        assert counts_towards_coverage(MissClass.HIT)
        assert counts_towards_coverage(MissClass.DETECTION_GAP)
        assert counts_towards_coverage(MissClass.VISIBILITY_GAP)
