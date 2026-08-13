import dataclasses
import re
from datetime import datetime, timezone

import pytest

from purple.battleboard.sanitize import (
    Disclosure,
    PublicAttackState,
    PublicBattleEvent,
    build_attack_label_map,
    format_score_fraction,
    project_instructor_event,
    project_public_event,
)
from purple.evaluation.evaluator import ActionResult, ActionState, EvidenceLevel

NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)

#: SA §5.4 不得公開清單。這裡比對的是欄位名稱，因為公開事件的防線是「根本沒有這個欄位」。
FORBIDDEN_FIELD_TOKENS = (
    "threshold",
    "payload",
    "query",
    "secret",
    "ttl",
    "ip",
    "rule",
)


def result(action_id, state, **overrides):
    values = dict(
        action_id=action_id,
        state=state,
        level=EvidenceLevel.C2 if state is ActionState.HIT else None,
        gap="detection_gap" if state is ActionState.MISS else None,
        reason="測試用",
        event_ids=("evt-1",) if state is ActionState.HIT else (),
    )
    values.update(overrides)
    return ActionResult(**values)


def test_public_event_has_no_forbidden_fields():
    """SA §5.4 七個禁見欄位在 schema 上不存在，不是靠執行期過濾。"""
    field_names = {f.name.lower() for f in dataclasses.fields(PublicBattleEvent)}
    for token in FORBIDDEN_FIELD_TOKENS:
        offenders = [name for name in field_names if token in name]
        assert offenders == [], f"公開事件不得有含 {token!r} 的欄位，發現 {offenders}"


def test_real_mitre_technique_never_appears_in_public_event():
    """Q3：公開層只有 `Attack #N`，真實 MITRE ID 不得出現在任何欄位值裡。"""
    labels = build_attack_label_map(["a-1", "a-2"])
    event = project_public_event(
        result("a-1", ActionState.MISS),
        attack_label=labels["a-1"],
        team="red",
        round_ended=True,
    )
    serialized = repr(dataclasses.asdict(event))
    assert re.search(r"\bT\d{4}(\.\d{3})?\b", serialized) is None
    assert event.attack_label == "Attack #1"


def test_state_is_withheld_until_round_ends():
    """Q4：回合結束前，被打穿的動作在公開層看起來與其他動作沒有差別。"""
    breached = result("a-1", ActionState.MISS)
    defended = result("a-2", ActionState.HIT)

    live = [
        project_public_event(r, attack_label="Attack #1", team="red", round_ended=False)
        for r in (breached, defended)
    ]
    assert {e.state for e in live} == {PublicAttackState.UNRESOLVED}
    assert {e.disclosure for e in live} == {Disclosure.PENDING}


def test_state_is_revealed_after_round_ends():
    breached = project_public_event(
        result("a-1", ActionState.MISS), attack_label="Attack #1", team="red", round_ended=True
    )
    defended = project_public_event(
        result("a-2", ActionState.HIT), attack_label="Attack #2", team="red", round_ended=True
    )

    assert breached.state is PublicAttackState.BREACHED
    assert defended.state is PublicAttackState.DEFENDED
    assert breached.disclosure is Disclosure.REVEALED


def test_instructor_sees_live_state_while_public_is_pending():
    """Q4 選的是 (b)：Instructor 不受延遲揭露限制。"""
    breached = result("a-1", ActionState.MISS)

    public = project_public_event(
        breached, attack_label="Attack #1", team="red", round_ended=False
    )
    instructor = project_instructor_event(breached, attack_label="Attack #1", team="red")

    assert public.state is PublicAttackState.UNRESOLVED
    assert instructor.state is PublicAttackState.BREACHED


def test_score_shows_denominator_and_never_bare_percentage():
    """§3.9：公開層只能是「看得見分母的分數」。"""
    assert format_score_fraction(8, 10) == "8/10"
    assert "%" not in format_score_fraction(8, 10)


def test_zero_denominator_returns_none_instead_of_faking_a_score():
    assert format_score_fraction(0, 0) is None


def test_label_map_follows_registry_order_not_technique_order():
    """標籤順序若隨技法排序，順序本身會洩漏技法分類。"""
    labels = build_attack_label_map(["z-last", "a-first"])
    assert labels == {"z-last": "Attack #1", "a-first": "Attack #2"}


def test_unknown_and_not_executed_are_indistinguishable_in_public():
    """公開層不區分「來源掛了無法判定」與「紅隊沒打」——兩者都只是還沒有結果。"""
    unknown = project_public_event(
        result("a-1", ActionState.UNKNOWN), attack_label="Attack #1", team="red", round_ended=True
    )
    not_executed = project_public_event(
        result("a-2", ActionState.NOT_EXECUTED),
        attack_label="Attack #2",
        team="red",
        round_ended=True,
    )
    assert unknown.state is not_executed.state is PublicAttackState.UNRESOLVED


def test_numerator_cannot_exceed_denominator():
    with pytest.raises(ValueError):
        format_score_fraction(11, 10)
