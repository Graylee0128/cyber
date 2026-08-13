"""#26 畫面一 Coverage 表投影契約。"""

from purple.console.coverage import (
    ActionOutcome,
    BlueMark,
    RedMark,
    TechniqueMeta,
    build_coverage_matrix,
)
from purple.evaluation.evaluator import ActionState

TECHS = {
    "T1190": TechniqueMeta("T1190", "Exploit Public-Facing Application", "initial-access"),
    "T1059": TechniqueMeta("T1059", "Command and Scripting Interpreter", "execution"),
    "T1005": TechniqueMeta("T1005", "Data from Local System", "collection",
                           note="僅代表敏感檔被開啟，未證明內容外流"),
}


def _row(rows, tid):
    return next(r for r in rows if r.technique_id == tid)


def test_only_registered_techniques_appear():
    # outcomes 只涉及 T1190；techniques.yaml 有三個，表格只出現 T1190。
    rows = build_coverage_matrix([ActionOutcome("a1", "T1190", ActionState.HIT)], TECHS)
    assert [r.technique_id for r in rows] == ["T1190"]


def test_not_executed_shows_未執行_not_check():
    rows = build_coverage_matrix([ActionOutcome("a1", "T1059", ActionState.NOT_EXECUTED)], TECHS)
    row = _row(rows, "T1059")
    assert row.red == RedMark.NOT_EXECUTED       # 「未執行」，不是 ✅
    assert row.red.value == "未執行"
    assert row.blue == BlueMark.NOT_APPLICABLE   # 藍隊無從偵測 → —


def test_not_executed_is_not_rendered_as_miss():
    # 兩者被合併成 ❌ 時，這條要變紅。
    rows = build_coverage_matrix([ActionOutcome("a1", "T1059", ActionState.NOT_EXECUTED)], TECHS)
    assert _row(rows, "T1059").blue != BlueMark.MISSED


def test_not_executed_does_not_count_toward_denominator():
    rows = build_coverage_matrix([ActionOutcome("a1", "T1059", ActionState.NOT_EXECUTED)], TECHS)
    assert _row(rows, "T1059").counts_toward_coverage is False


def test_unknown_does_not_count_toward_denominator():
    rows = build_coverage_matrix([ActionOutcome("a1", "T1059", ActionState.UNKNOWN)], TECHS)
    row = _row(rows, "T1059")
    assert row.blue == BlueMark.UNKNOWN
    assert row.counts_toward_coverage is False


def test_hit_and_miss_count_toward_denominator():
    rows = build_coverage_matrix(
        [ActionOutcome("a1", "T1190", ActionState.HIT),
         ActionOutcome("a2", "T1059", ActionState.MISS)],
        TECHS,
    )
    assert _row(rows, "T1190").counts_toward_coverage is True
    assert _row(rows, "T1059").counts_toward_coverage is True


def test_miss_dominates_hit_so_a_gap_is_never_whitewashed():
    # 同 technique 一次 hit、一次 miss → 藍欄必須顯示 MISSED，不能被 hit 洗成 DETECTED。
    rows = build_coverage_matrix(
        [ActionOutcome("a1", "T1059", ActionState.HIT),
         ActionOutcome("a2", "T1059", ActionState.MISS)],
        TECHS,
    )
    assert _row(rows, "T1059").blue == BlueMark.MISSED


def test_technique_note_is_carried():
    rows = build_coverage_matrix([ActionOutcome("a1", "T1005", ActionState.HIT)], TECHS)
    assert _row(rows, "T1005").note == "僅代表敏感檔被開啟，未證明內容外流"


def test_console_does_not_invent_a_fifth_blue_state():
    # 四態映到恰好四種藍欄符號，不多不少。
    seen = set()
    for state in ActionState:
        rows = build_coverage_matrix([ActionOutcome("a", "T1190", state)], TECHS)
        seen.add(_row(rows, "T1190").blue)
    assert seen == {BlueMark.DETECTED, BlueMark.MISSED, BlueMark.UNKNOWN, BlueMark.NOT_APPLICABLE}
