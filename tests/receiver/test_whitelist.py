"""Technique 白名單治理 —— 純函數，真 TDD（票 04）。"""

from purple.receiver.whitelist import (
    TechniqueRejected,
    Whitelist,
    WhitelistError,
    check_technique,
    default_whitelist,
    parse_whitelist,
)

import pytest

GOOD = {
    "techniques": [
        {"id": "T1190", "name": "Exploit Public-Facing Application", "tactic": "initial-access"},
        {"id": "T1110", "name": "Brute Force", "tactic": "credential-access", "note": "需與成功登入證據連結"},
    ]
}


class TestRejection:
    def test_technique_outside_whitelist_rejected(self):
        wl = parse_whitelist(GOOD)
        with pytest.raises(TechniqueRejected, match="T9999"):
            check_technique("T9999", wl)

    def test_whitelisted_technique_passes(self):
        wl = parse_whitelist(GOOD)
        check_technique("T1190", wl)  # 不拋即通過


class TestLevelConsistency:
    def test_parent_and_subtechnique_together_is_rejected(self):
        """不可一邊 T1190、另一邊 T1190.001 —— coverage 會重複計數。"""
        mixed = {"techniques": [
            {"id": "T1190", "name": "x", "tactic": "t"},
            {"id": "T1190.001", "name": "y", "tactic": "t"},
        ]}
        with pytest.raises(WhitelistError, match="層級不一致"):
            parse_whitelist(mixed)

    def test_subtechnique_alone_is_fine(self):
        only_sub = {"techniques": [{"id": "T1190.001", "name": "y", "tactic": "t"}]}
        assert parse_whitelist(only_sub).allows("T1190.001")

    def test_duplicate_id_is_rejected(self):
        dup = {"techniques": [
            {"id": "T1190", "name": "x", "tactic": "t"},
            {"id": "T1190", "name": "x2", "tactic": "t"},
        ]}
        with pytest.raises(WhitelistError, match="重複"):
            parse_whitelist(dup)

    def test_empty_whitelist_is_rejected(self):
        with pytest.raises(WhitelistError, match="空"):
            parse_whitelist({"techniques": []})


class TestNote:
    def test_note_is_carried(self):
        wl = parse_whitelist(GOOD)
        assert "成功登入" in wl.note("T1110")

    def test_missing_note_is_none(self):
        wl = parse_whitelist(GOOD)
        assert wl.note("T1190") is None


class TestShippedWhitelist:
    """實際 config/techniques.yaml 必須自洽，且涵蓋各 scenario 用到的 technique。"""

    def test_default_whitelist_loads(self):
        wl = default_whitelist()
        assert isinstance(wl, Whitelist)

    @pytest.mark.parametrize("tid", ["T1190", "T1078", "T1110", "T1059"])
    def test_covers_the_p1_techniques(self, tid):
        assert default_whitelist().allows(tid)

    def test_t1110_note_warns_about_interpretation(self):
        assert default_whitelist().note("T1110")
