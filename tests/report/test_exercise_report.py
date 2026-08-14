"""#28 Exercise Report 契約 —— 三個不可省欄位、缺口分類、快照穩定、無 Detection Rate。"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.metrics.gaps import MissClass
from purple.report.exercise_report import (
    BlueSummary,
    CoverageGap,
    ExerciseReport,
    RedSummary,
    ReportContractError,
    UnknownSummary,
    build_exercise_report,
)
from purple.retention.window import RetentionWindow

METRICS = {
    "action_coverage": 0.82,
    "confirmation_rate": 0.9,
    "alert_volume": 4120,
    "excluded_counts": {"unknown": 2, "not_executed": 1},
}

EXERCISE_START = datetime(2026, 8, 13, 9, 10, tzinfo=timezone.utc)
EXERCISE_END = datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc)
RETENTION = RetentionWindow(exercise_start=EXERCISE_START, exercise_end=EXERCISE_END)


def _report(**over):
    kw = dict(
        exercise_id="ex-01",
        metrics=METRICS,
        red=RedSummary(attack_success_pct=67, objectives_completed=4, objectives_total=7),
        coverage_gaps=(
            CoverageGap("T1059", MissClass.DETECTION_GAP),
            CoverageGap("T1071", MissClass.VISIBILITY_GAP),
        ),
        unknown_reasons=("Falco 於 14:52–14:58 掉線",),
        mttd_ms=12400,
        mttr_ms=14200,
        retention_window=RETENTION,
        event_timestamps=(EXERCISE_START + timedelta(minutes=5),),
        recommendations=("補 T1059 規則",),
    )
    kw.update(over)
    return build_exercise_report(**kw)


# ── 三個不可省欄位 ───────────────────────────────────────────────────────
def test_alert_volume_sits_beside_coverage():
    d = _report().as_dict()
    assert d["blue"]["action_coverage"] == 0.82
    assert d["blue"]["alert_volume"] == 4120   # 與 coverage 並列


def test_blue_summary_cannot_omit_alert_volume():
    # 型別層就沒有「只有 coverage」的建構路徑。
    with pytest.raises(TypeError):
        BlueSummary(action_coverage=0.82, mttd_ms=1, mttr_ms=1)  # 少 alert_volume


def test_missing_alert_volume_in_metrics_fails_loud():
    with pytest.raises(ReportContractError):
        _report(metrics={"action_coverage": 0.82, "excluded_counts": {"unknown": 0}})


def test_every_gap_is_classified():
    d = _report().as_dict()
    classes = {g["classification"] for g in d["coverage_gaps"]}
    assert classes == {"detection_gap", "visibility_gap"}


def test_gap_cannot_be_unclassified():
    # hit / unknown / out_of_scope 不是缺口，不能塞進缺口清單。
    for bad in (MissClass.HIT, MissClass.UNKNOWN, MissClass.OUT_OF_SCOPE):
        with pytest.raises(ReportContractError):
            CoverageGap("T1190", bad)


def test_unknown_carries_count_and_reasons():
    d = _report().as_dict()
    assert d["unknown"]["count"] == 2
    assert d["unknown"]["reasons"] == ["Falco 於 14:52–14:58 掉線"]


def test_unknown_count_without_reasons_is_rejected():
    with pytest.raises(ReportContractError):
        UnknownSummary(count=2, reasons=())


def test_zero_unknown_needs_no_reasons():
    assert UnknownSummary(count=0, reasons=()).count == 0


# ── 快照穩定：數字取自 API、之後不隨資料變動 ─────────────────────────────
def test_numbers_come_from_api_metrics():
    d = _report().as_dict()
    assert d["blue"]["action_coverage"] == METRICS["action_coverage"]
    assert d["blue"]["alert_volume"] == METRICS["alert_volume"]


def test_report_is_frozen_after_build():
    report = _report()
    with pytest.raises(Exception):
        report.blue.alert_volume = 0  # frozen dataclass


def test_mutating_source_metrics_does_not_change_the_report():
    live = dict(METRICS, excluded_counts=dict(METRICS["excluded_counts"]))
    report = build_exercise_report(
        exercise_id="ex-01", metrics=live,
        red=RedSummary(67, 4, 7),
        coverage_gaps=(CoverageGap("T1059", MissClass.DETECTION_GAP),),
        unknown_reasons=("x",), mttd_ms=1, mttr_ms=1,
        retention_window=RETENTION,
    )
    live["alert_volume"] = 0
    live["excluded_counts"]["unknown"] = 999
    assert report.blue.alert_volume == 4120
    assert report.unknown.count == 2


# ── 禁止欄位 / raw 覆蓋 ──────────────────────────────────────────────────
def test_report_never_contains_detection_rate():
    d = _report().as_dict()
    assert "detection_rate" not in str(d).replace(" ", "_").lower()


def test_detection_rate_key_is_actively_rejected():
    bad = ExerciseReport(
        exercise_id="ex", red=RedSummary(1, 1, 1),
        blue=BlueSummary(0.5, 10, 1, 1),
        coverage_gaps=(), unknown=UnknownSummary(0, ()),
        raw_coverage_windows=(), recommendations=("Detection Rate: 50%",),
    )
    # recommendations 是自由字串，允許提到字樣；但結構化的 detection_rate key 要被擋。
    # 這裡直接驗 _reject 對一個帶該 key 的 dict 會爆。
    from purple.report.exercise_report import _reject_detection_rate
    with pytest.raises(ReportContractError):
        _reject_detection_rate({"blue": {"detection_rate": 0.5}})
    assert bad.as_dict()["exercise_id"] == "ex"


def test_raw_coverage_window_is_marked_not_silently_blank():
    d = _report().as_dict()
    assert d["raw_coverage_windows"] == ["09:15 有 raw"]


def test_raw_coverage_window_is_computed_not_caller_supplied():
    """票 #98 項目 3：過期判定由 retention_window 算，不是呼叫端手填字串。"""
    within = EXERCISE_START + timedelta(minutes=5)
    expired = EXERCISE_START - timedelta(hours=2)  # 遠早於保留窗開窗時間

    d = _report(event_timestamps=(within, expired)).as_dict()

    assert d["raw_coverage_windows"] == [
        f"{expired:%H:%M} raw 已過期",
        f"{within:%H:%M} 有 raw",
    ]


def test_no_referenced_events_yields_no_windows():
    d = _report(event_timestamps=()).as_dict()
    assert d["raw_coverage_windows"] == []
