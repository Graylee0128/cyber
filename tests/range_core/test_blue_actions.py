"""Blue Action 的封閉列舉與「判讀一次定生死」（#49／WS3 spec §4.1、§4.2）。

純領域測試，不碰 PG、不碰 HTTP。
"""

from datetime import datetime, timedelta, timezone

import pytest

from range_core.blue_actions import (
    BlueAction,
    BlueActionLog,
    BlueActionRejected,
    BlueActionType,
    build_action,
    parse_action,
)

T0 = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def _action(kind, event_id="evt-1", offset_s=0, technique=None) -> BlueAction:
    return build_action(kind, event_id, T0 + timedelta(seconds=offset_s), technique)


class TestClosedEnumeration:
    def test_the_five_actions_are_the_whole_list(self):
        assert {a.value for a in BlueActionType} == {
            "acknowledge",
            "classify",
            "contain",
            "resolve",
            "dismiss",
        }

    def test_escalate_is_rejected(self):
        """驗收條件：五個動作外加一個 escalate，必須被拒絕。"""
        with pytest.raises(BlueActionRejected) as exc:
            parse_action("escalate")
        assert "escalate" in str(exc.value)

    def test_rejection_names_the_allowed_values(self):
        """靜默丟棄會讓藍隊以為自己送出去了 —— 拒絕要說得出原因。"""
        with pytest.raises(BlueActionRejected) as exc:
            parse_action("unblock")
        assert "acknowledge" in str(exc.value) and "dismiss" in str(exc.value)


class TestActionShape:
    def test_records_action_time_and_event_id(self):
        action = _action("acknowledge")
        assert (action.action, action.event_id, action.submitted_at) == (
            BlueActionType.ACKNOWLEDGE,
            "evt-1",
            T0,
        )

    def test_no_per_user_field_exists(self):
        """WS3 §5.1：「誰」恆為 blue（隊，不是人），不存一個永遠相同的值。"""
        fields = set(vars(_action("acknowledge")))
        assert not fields & {"player_id", "user", "user_id", "team", "analyst"}

    def test_action_must_reference_an_event(self):
        with pytest.raises(BlueActionRejected):
            build_action("acknowledge", "", T0)

    def test_classify_must_carry_a_technique(self):
        with pytest.raises(BlueActionRejected):
            build_action("classify", "evt-1", T0)

    def test_other_actions_must_not_carry_a_technique(self):
        with pytest.raises(BlueActionRejected):
            build_action("contain", "evt-1", T0, technique="T1190")


class TestOneShotJudgement:
    """WS3 §4.2：`classify` 與 `dismiss` 每個 event 只有一次機會。"""

    def test_second_classify_on_the_same_event_is_rejected(self):
        log = BlueActionLog([_action("classify", technique="T1190")])
        with pytest.raises(BlueActionRejected) as exc:
            log.record(_action("classify", offset_s=5, technique="T1110"))
        assert "one shot" in str(exc.value)

    def test_second_dismiss_is_rejected(self):
        log = BlueActionLog([_action("dismiss")])
        with pytest.raises(BlueActionRejected):
            log.record(_action("dismiss", offset_s=5))

    def test_dismiss_after_classify_is_rejected(self):
        """兩者共用同一個名額 —— 否則就是「先猜誤報，被打槍再猜技法」。"""
        log = BlueActionLog([_action("classify", technique="T1190")])
        with pytest.raises(BlueActionRejected):
            log.record(_action("dismiss", offset_s=5))

    def test_judgement_on_a_different_event_is_fine(self):
        log = BlueActionLog([_action("classify", technique="T1190")])
        log.record(_action("classify", event_id="evt-2", technique="T1110"))
        assert len(log.actions) == 2

    def test_non_judgement_actions_may_repeat(self):
        """重按封鎖鈕不該報錯；計分只看第一次。"""
        log = BlueActionLog([_action("contain"), _action("contain", offset_s=10)])
        assert log.first("evt-1", BlueActionType.CONTAIN).submitted_at == T0

    def test_first_judgement_is_the_one_that_counts(self):
        log = BlueActionLog([_action("classify", technique="T1190")])
        assert log.judgement_for("evt-1").technique == "T1190"
