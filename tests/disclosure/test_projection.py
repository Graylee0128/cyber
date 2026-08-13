"""欄位級遮蔽（#49／WS3 spec §2）—— 表是資料，投影是唯一的套用點。

把 `FIELD_MASKING` 清空（模擬「遮蔽被拿掉」）時，`TestMaskingRemovedGoesRed`
底下每一條都要變紅。
"""

import pytest

from disclosure import (
    CALLER_CLEARANCE,
    FIELD_MASKING,
    FieldPolicy,
    MaskStrategy,
    build_label_map,
    project_fields,
)

RULE_TITLES = ("SQLInjectionBurst", "SSHBruteForce", "FalcoCommandExec")
LABELS = {"rule": build_label_map(RULE_TITLES, "Detection")}

EVENT = {
    "event_id": "evt-1",
    "rule": "SSHBruteForce",
    "technique": "T1110",
    "severity": "high",
}


def _project(identity: str, payload=EVENT):
    return project_fields(payload, CALLER_CLEARANCE[identity], labels=LABELS)


class TestTechniqueMasking:
    @pytest.mark.parametrize("identity", ["red", "blue"])
    def test_red_and_blue_do_not_receive_technique(self, identity):
        """WS3 §2.1／§2.2：藍隊要自己判讀，紅隊一起遮。"""
        assert "technique" not in _project(identity)

    @pytest.mark.parametrize("identity", ["purple", "instructor"])
    def test_purple_and_instructor_still_see_technique(self, identity):
        """評分與分析需要真值。"""
        assert _project(identity)["technique"] == "T1110"


class TestRuleNameIsNotABypass:
    """繞道防護：rule 名稱本身就是答案（`SSHBruteForce` ＝ T1110 Brute Force）。"""

    @pytest.mark.parametrize("identity", ["red", "blue"])
    def test_real_rule_name_never_reaches_red_or_blue(self, identity):
        assert "SSHBruteForce" not in str(_project(identity))

    def test_blue_gets_a_stable_anonymous_label_instead(self):
        assert _project("blue")["rule"] == "Detection #2"

    def test_same_rule_always_maps_to_the_same_label(self):
        first = _project("blue")["rule"]
        second = project_fields(
            {"rule": "SSHBruteForce"}, CALLER_CLEARANCE["blue"], labels=LABELS
        )["rule"]
        assert first == second

    def test_labels_follow_registration_order_not_alphabetical(self):
        """字母序會讓標籤順序本身洩漏分類。"""
        labels = build_label_map(RULE_TITLES, "Detection")
        assert labels["SQLInjectionBurst"] == "Detection #1"
        assert labels["FalcoCommandExec"] == "Detection #3"

    def test_unknown_rule_is_dropped_not_passed_through(self):
        """查不到標籤 → 整個欄位拿掉。少一個欄位，不會多一個答案。"""
        projected = project_fields(
            {"rule": "BrandNewRule"}, CALLER_CLEARANCE["blue"], labels=LABELS
        )
        assert "rule" not in projected

    def test_no_label_map_at_all_still_never_leaks(self):
        assert "rule" not in project_fields({"rule": "SSHBruteForce"}, CALLER_CLEARANCE["blue"])


class TestProjectionIsNonDestructive:
    def test_input_payload_is_not_mutated(self):
        """落地內容不變 —— 遮蔽只作用在要回給誰的那份 dict。"""
        payload = dict(EVENT)
        project_fields(payload, CALLER_CLEARANCE["blue"], labels=LABELS)
        assert payload == EVENT

    def test_unlisted_fields_pass_through_untouched(self):
        assert _project("blue")["severity"] == "high"


class TestMaskingTableIsData:
    def test_adding_a_field_needs_no_logic_change(self):
        """新增一個要遮的欄位＝加一列表，投影函式一個字不用改。"""
        masking = {"target": FieldPolicy("purple", MaskStrategy.DROP)}
        projected = project_fields(
            {"target": "target-01"}, CALLER_CLEARANCE["blue"], masking=masking
        )
        assert "target" not in projected

    def test_shipped_table_covers_both_known_leak_channels(self):
        assert set(FIELD_MASKING) == {"technique", "rule"}


class TestMaskingRemovedGoesRed:
    """驗收條件：把遮蔽拿掉時，上面的測試必須變紅。這裡直接證明空表會漏。"""

    def test_empty_masking_table_leaks_everything(self):
        leaked = project_fields(EVENT, CALLER_CLEARANCE["blue"], labels=LABELS, masking={})
        assert leaked["technique"] == "T1110"
        assert leaked["rule"] == "SSHBruteForce"
