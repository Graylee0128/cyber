"""藍隊計分（#49／WS3 spec §4.3、§4.5）—— 平台級參數、兩段共用起點、自動判讀比對。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from range_core.blue_actions import (
    BlueActionLog,
    MappingDispatchOutcome,
    MappingExecutionEvidence,
    MappingTechniqueTruth,
    build_action,
)
from range_core.blue_scoring import (
    DEFAULT_CONFIG_PATH,
    BlueScoringConfig,
    BlueScoringConfigError,
    derive_blue_scores,
    score_event,
)

T0 = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
CONFIG = BlueScoringConfig.load(DEFAULT_CONFIG_PATH)
TRUTH = MappingTechniqueTruth({"evt-1": "T1190"})
NO_EVIDENCE = MappingExecutionEvidence({})
HAS_EVIDENCE = MappingExecutionEvidence({"evt-1": True})


def _log(*actions) -> BlueActionLog:
    return BlueActionLog(
        build_action(kind, "evt-1", T0 + timedelta(seconds=offset), technique)
        for kind, offset, technique in actions
    )


def _score(log, truth=TRUTH, evidence=NO_EVIDENCE):
    return score_event("evt-1", T0, log, CONFIG, truth, evidence)


class TestPlatformLevelConfig:
    def test_shipped_config_has_the_four_objectives_and_the_threshold(self):
        assert set(CONFIG.points) == {
            "detect_attack",
            "identify_technique",
            "contain",
            "resolve_incident",
        }
        assert CONFIG.contain_threshold_seconds == 60

    def test_scenario_files_cannot_change_the_threshold(self):
        """驗收條件：改動 scenario 檔不影響門檻 —— 設定根本不從那裡讀。"""
        scenario_dir = Path(DEFAULT_CONFIG_PATH).resolve().parents[1] / "scenarios"
        assert DEFAULT_CONFIG_PATH.parent.name == "config"
        assert scenario_dir not in DEFAULT_CONFIG_PATH.parents

    def test_broken_config_fails_loud(self, tmp_path):
        """靜默用預設值＝偷偷換一把尺。"""
        path = tmp_path / "blue.yaml"
        path.write_text("objectives: {detect_attack: 100}\n", encoding="utf-8")
        with pytest.raises(BlueScoringConfigError):
            BlueScoringConfig.load(path)

    def test_missing_config_fails_loud(self, tmp_path):
        with pytest.raises(BlueScoringConfigError):
            BlueScoringConfig.load(tmp_path / "nope.yaml")


class TestTwoSegmentsShareOneStart:
    """WS3 §4.5：起點永遠是 Core Event 的 observed_at，不是接力。"""

    def test_detect_measures_core_event_to_acknowledge(self):
        assert _score(_log(("acknowledge", 12, None))).detect_seconds == 12

    def test_contain_measures_core_event_to_contain(self):
        assert _score(_log(("contain", 40, None))).contain_seconds == 40

    def test_skipping_acknowledge_still_scores_contain(self):
        """驗收條件：藍隊跳過 acknowledge 直接 contain，Contain < 60 仍成立。"""
        score = _score(_log(("contain", 30, None)))
        assert score.detect_seconds is None
        assert score.awarded == CONFIG.points["contain"]

    def test_contain_is_not_measured_from_acknowledge(self):
        """acknowledge 很慢、contain 緊接其後：若誤用接力，會錯誤地給分。"""
        score = _score(_log(("acknowledge", 300, None), ("contain", 305, None)))
        assert score.contain_seconds == 305
        assert score.awarded == CONFIG.points["detect_attack"]  # contain 超時，沒有 150

    def test_contain_after_threshold_scores_nothing_for_contain(self):
        assert _score(_log(("contain", 61, None))).awarded == 0

    def test_reaction_time_is_unavailable_not_zero_when_blue_did_nothing(self):
        """串位防護：攻擊自己停止、藍隊沒動作 → 不可得，不是 0。"""
        score = _score(BlueActionLog())
        assert score.detect_seconds is None and score.contain_seconds is None
        assert score.reaction_time_available is False


class TestContainRequiresSuccessfulDispatch:
    """#51／WS3 spec §5.2：不得出現「有分數沒封鎖」——`dispatch=None`（預設）
    是 #49 原本沒有派送概念時的行為，不受影響；真的傳 `dispatch` 才會被 gate。
    """

    def test_no_dispatch_argument_keeps_the_old_behavior(self):
        """向下相容：不傳 dispatch，contain 一樣照時間門檻計分——#49 的既有
        呼叫端與測試不必為了這個新概念改寫。"""
        score = score_event("evt-1", T0, _log(("contain", 30, None)), CONFIG, TRUTH, NO_EVIDENCE)
        assert score.awarded == CONFIG.points["contain"]

    def test_dispatched_within_threshold_scores(self):
        dispatch = MappingDispatchOutcome({"evt-1": True})
        score = score_event(
            "evt-1", T0, _log(("contain", 30, None)), CONFIG, TRUTH, NO_EVIDENCE, dispatch=dispatch
        )
        assert score.awarded == CONFIG.points["contain"]

    def test_failed_dispatch_within_threshold_scores_nothing(self):
        """時間對、但沒真的派送成功——不給分。這是 AC 明講的那條線。"""
        dispatch = MappingDispatchOutcome({"evt-1": False})
        score = score_event(
            "evt-1", T0, _log(("contain", 30, None)), CONFIG, TRUTH, NO_EVIDENCE, dispatch=dispatch
        )
        assert score.awarded == 0
        assert score.contain_seconds == 30  # 時間仍然算得出來，只是不給分

    def test_failed_dispatch_does_not_affect_other_objectives(self):
        """contain 沒分不該連坐扣掉 detect_attack——兩個 objective 各自獨立。"""
        dispatch = MappingDispatchOutcome({"evt-1": False})
        score = score_event(
            "evt-1", T0, _log(("acknowledge", 5, None), ("contain", 30, None)),
            CONFIG, TRUTH, NO_EVIDENCE, dispatch=dispatch,
        )
        assert score.awarded == CONFIG.points["detect_attack"]


class TestJudgementScoring:
    def test_correct_classify_scores_identify_technique(self):
        score = _score(_log(("classify", 20, "T1190")))
        assert score.judgement == "correct"
        assert score.awarded == CONFIG.points["identify_technique"]

    def test_wrong_classify_scores_zero_for_that_objective(self):
        """一次定生死：猜錯就是零分，沒有遞減、沒有重試。"""
        score = _score(_log(("classify", 20, "T1110")))
        assert score.judgement == "wrong"
        assert score.awarded == 0

    def test_classify_without_a_known_answer_is_not_a_free_point(self):
        score = _score(_log(("classify", 20, "T1190")), truth=MappingTechniqueTruth({}))
        assert score.judgement == "wrong"
        assert score.awarded == 0


class TestDismissIsAdjudicatedByExecutionEvidence:
    """WS3 §4.1：誤報與否的真相來源是執行證據，**不是**偵測規則的結果。"""

    def test_no_execution_evidence_means_the_dismiss_was_right(self):
        assert _score(_log(("dismiss", 15, None))).judgement == "dismissed_correctly"

    def test_execution_evidence_means_the_dismiss_was_wrong(self):
        score = _score(_log(("dismiss", 15, None)), evidence=HAS_EVIDENCE)
        assert score.judgement == "dismissed_wrongly"

    def test_dismiss_does_not_consult_the_detection_answer(self):
        """把答案來源換成會爆炸的東西：dismiss 的裁決仍算得出來（沒有循環論證）。"""

        class Exploding:
            def technique_for(self, event_id):
                raise AssertionError("dismiss must not consult the detection result")

        assert _score(_log(("dismiss", 15, None)), truth=Exploding()).judgement == (
            "dismissed_correctly"
        )


class TestAggregation:
    def test_event_level_records_aggregate_into_a_total(self):
        """WS5 §1.2：記在 event 級，粗粒度統計由聚合推導。"""
        log = BlueActionLog(
            [
                build_action("acknowledge", "evt-1", T0 + timedelta(seconds=5)),
                build_action("acknowledge", "evt-2", T0 + timedelta(seconds=9)),
            ]
        )
        board = derive_blue_scores({"evt-1": T0, "evt-2": T0}, log, CONFIG, TRUTH, NO_EVIDENCE)
        assert board.total == CONFIG.points["detect_attack"] * 2
        assert {e.event_id for e in board.events} == {"evt-1", "evt-2"}

    def test_scoreboard_shape_is_additive_to_the_red_one(self):
        board = derive_blue_scores({"evt-1": T0}, BlueActionLog(), CONFIG, TRUTH, NO_EVIDENCE)
        assert set(board.as_dict()) == {"blue"}
