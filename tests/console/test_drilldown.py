"""#26 畫面二 Technique 下鑽投影契約。"""

from purple.console.drilldown import (
    Detection,
    TelemetryMark,
    build_drilldown,
    read_gap,
    telemetry_mark,
)
from purple.metrics.gaps import MissClass
from purple.registry.source_registry import SourceState


# ── 三條判讀規則（Telemetry 欄 + Detection → 缺口分類）─────────────────────
def test_all_no_event_and_no_detection_is_visibility_gap():
    marks = [TelemetryMark.NO_EVENT, TelemetryMark.NO_EVENT]
    assert read_gap(marks, detected=False) == MissClass.VISIBILITY_GAP


def test_some_present_and_no_detection_is_detection_gap():
    marks = [TelemetryMark.PRESENT, TelemetryMark.NO_EVENT]
    assert read_gap(marks, detected=False) == MissClass.DETECTION_GAP


def test_some_present_and_detection_is_hit():
    marks = [TelemetryMark.PRESENT]
    assert read_gap(marks, detected=True) == MissClass.HIT


# ── ❌ 與 — 不可混用 ─────────────────────────────────────────────────────
def test_not_deployed_and_no_event_are_distinct_marks():
    assert telemetry_mark(SourceState.ABSENT, event_present=False) == TelemetryMark.NOT_DEPLOYED
    assert telemetry_mark(SourceState.ABSENT, event_present=True) == TelemetryMark.NOT_DEPLOYED
    assert telemetry_mark(SourceState.HEALTHY, event_present=False) == TelemetryMark.NO_EVENT
    assert telemetry_mark(SourceState.HEALTHY, event_present=True) == TelemetryMark.PRESENT
    assert telemetry_mark(SourceState.STALE, event_present=False) == TelemetryMark.UNKNOWN


def test_removing_a_deployment_flips_x_to_dash_and_does_not_worsen():
    # 冤枉防護：唯一那個 ❌ 的來源撤除部署 → 變 —，分類從可見性缺口降為「不在範圍」，不惡化。
    before = read_gap([TelemetryMark.NO_EVENT], detected=False)
    after = read_gap([TelemetryMark.NOT_DEPLOYED], detected=False)
    assert before == MissClass.VISIBILITY_GAP
    assert after == MissClass.OUT_OF_SCOPE          # 不是缺口 → 沒有惡化
    assert after != MissClass.DETECTION_GAP         # 尤其不能被說成更嚴重的偵測缺口


def test_absent_source_never_drags_a_hit_down():
    # 一個 present（healthy 有事件）＋ 一個 absent，且有偵測 → 仍是 HIT，— 不參與判定。
    marks = [TelemetryMark.PRESENT, TelemetryMark.NOT_DEPLOYED]
    assert read_gap(marks, detected=True) == MissClass.HIT
    assert read_gap(marks, detected=False) == MissClass.DETECTION_GAP


def test_all_deployed_sources_stale_is_unknown():
    marks = [TelemetryMark.UNKNOWN, TelemetryMark.NOT_DEPLOYED]
    assert read_gap(marks, detected=False) == MissClass.UNKNOWN


# ── 畫面與引擎不得各算各的 ───────────────────────────────────────────────
def test_display_gap_matches_engine_gap():
    d = build_drilldown(
        technique_id="T1059",
        observed_at="14:31:04",
        sources=[("app-log", SourceState.HEALTHY, True), ("falco", SourceState.HEALTHY, False)],
        detection=Detection(rule=None, latency_ms=None),
        engine_gap=MissClass.DETECTION_GAP,
    )
    # Telemetry 欄有一個 ✅ 一個 ❌、無偵測 → 讀出 DETECTION_GAP，與引擎給的一致。
    assert d.display_gap == d.gap == MissClass.DETECTION_GAP


def test_telemetry_column_is_built_from_source_states_not_hardcoded():
    d = build_drilldown(
        technique_id="T1190",
        observed_at=None,
        sources=[("waf", SourceState.ABSENT, False)],   # v0.2.1 沒部署 WAF
        detection=Detection(rule=None, latency_ms=None),
        engine_gap=MissClass.OUT_OF_SCOPE,
    )
    assert d.telemetry[0].source_id == "waf"
    assert d.telemetry[0].mark == TelemetryMark.NOT_DEPLOYED   # — 而非 ❌


def test_latency_fields_do_not_swap_positions():
    d = build_drilldown(
        technique_id="T1059",
        observed_at=None,
        sources=[("app-log", SourceState.HEALTHY, True)],
        detection=Detection(rule="sqli-alert", latency_ms=1200),
        engine_gap=MissClass.HIT,
        mttd_ms=1200,
        mttr_ms=14200,
        containment_ms=800,
    )
    assert d.mttd_ms == 1200
    assert d.mttr_ms == 14200
    assert d.containment_ms == 800
