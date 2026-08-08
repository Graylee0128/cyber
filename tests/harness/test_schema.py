"""Core Event schema 斷言 —— 純函數，真 TDD。

契約在 .scratch/p1-output-contract/spec.md §2。這裡把它變成可執行的斷言，
否則「符合契約」只是一句話。
"""

import pytest

from purple.harness.schema import SchemaViolation, assert_core_event

VALID = {
    "event_id": "evt-01J000000000000000000000",
    "exercise_id": "ex-001",
    "scenario_id": "sqli-01",
    "event_type": "attack.detected",
    "lifecycle": "firing",
    "severity": "high",
    "source": "grafana",
    "team": "red",
    "technique": "T1190",
    "target": {"service": "vulnerable-app"},
    "observed_at": "2026-08-08T14:30:00+08:00",
    "visibility": "public",
}


def without(field: str) -> dict:
    return {k: v for k, v in VALID.items() if k != field}


class TestAcceptsTheContract:
    def test_the_spec_example_passes(self):
        assert_core_event(VALID)

    def test_resolved_lifecycle_passes(self):
        assert_core_event({**VALID, "lifecycle": "resolved"})


class TestRequiredFields:
    @pytest.mark.parametrize("field", sorted(VALID))
    def test_every_field_is_required(self, field):
        with pytest.raises(SchemaViolation, match=field):
            assert_core_event(without(field))


class TestForbiddenFields:
    """spec §2.1 的禁用欄位。這一組是 ADR ④ 的執行機制 ——
    沒有它，`evidence_ref` 會在某次 PR 裡悄悄長回來。"""

    def test_evidence_ref_is_rejected(self):
        with pytest.raises(SchemaViolation, match="evidence_ref"):
            assert_core_event({**VALID, "evidence_ref": "loki://..."})

    @pytest.mark.parametrize("field", ["raw", "payload", "threshold", "query"])
    def test_telemetry_detail_is_rejected(self, field):
        with pytest.raises(SchemaViolation, match=field):
            assert_core_event({**VALID, field: "anything"})

    @pytest.mark.parametrize("word", ["loki", "logql", "promql", "LogQL"])
    def test_backend_vocabulary_is_rejected_anywhere_in_the_event(self, word):
        """換掉 Loki 時本 schema 一個字都不動 —— 所以字面上就不能出現。"""
        with pytest.raises(SchemaViolation, match="(?i)" + word):
            assert_core_event({**VALID, "target": {"service": f"x-{word}-y"}})

    def test_unknown_field_is_rejected(self):
        """未知欄位一律擋。契約不是最低要求，是完整清單。"""
        with pytest.raises(SchemaViolation, match="surprise"):
            assert_core_event({**VALID, "surprise": 1})


class TestEnums:
    def test_pending_lifecycle_is_rejected(self):
        """`pending` 是 Grafana 內部狀態，不是遊戲語意（spec §2.2）。"""
        with pytest.raises(SchemaViolation, match="pending"):
            assert_core_event({**VALID, "lifecycle": "pending"})

    def test_unknown_visibility_is_rejected(self):
        with pytest.raises(SchemaViolation, match="everyone"):
            assert_core_event({**VALID, "visibility": "everyone"})

    def test_unknown_event_type_is_rejected(self):
        with pytest.raises(SchemaViolation, match="attack.maybe"):
            assert_core_event({**VALID, "event_type": "attack.maybe"})


class TestVisibilityIsDerivedNotDeclared:
    """spec §2.4：visibility 由 event_type 決定，rule 不得覆寫。
    斷言層要抓到不一致，否則寫規則的人可以決定紅隊看得到什麼。"""

    def test_mismatched_visibility_is_rejected(self):
        with pytest.raises(SchemaViolation, match="visibility"):
            assert_core_event({**VALID, "event_type": "detection.miss", "visibility": "public"})

    @pytest.mark.parametrize(
        "event_type,visibility",
        [
            ("attack.detected", "public"),
            ("detection.hit", "blue"),
            ("detection.miss", "purple"),
            ("response.executed", "blue"),
            ("response.failed", "purple"),
            ("exercise.started", "instructor"),
        ],
    )
    def test_the_whole_mapping_is_enforced(self, event_type, visibility):
        assert_core_event({**VALID, "event_type": event_type, "visibility": visibility})


class TestTimestamps:
    def test_naive_observed_at_is_rejected(self):
        """同票 01：無時區的時間戳是本平台要消滅的東西。"""
        with pytest.raises(SchemaViolation, match="timezone"):
            assert_core_event({**VALID, "observed_at": "2026-08-08T14:30:00"})

    def test_unparsable_observed_at_is_rejected(self):
        with pytest.raises(SchemaViolation, match="observed_at"):
            assert_core_event({**VALID, "observed_at": "yesterday"})
