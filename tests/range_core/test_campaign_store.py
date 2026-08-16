"""#153 Campaign — `CampaignStateStore`. `exercise_store` (conftest.py) already
`ensure_schema`s and `truncate_all`s against `pg_connection`, so a
`CampaignStateStore` built on the same connection sees the same tables --
same pattern `test_blue_action_store.py` uses against `BlueActionStore`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from range_core.campaign_store import (
    AlreadyPaused,
    CampaignStateNotFound,
    CampaignStateStore,
    NotPaused,
)
from range_core.exercises import ExerciseStore, PlayerRegistration

from tests.range_core.test_exercises import roster, scenario


@pytest.fixture
def campaign_store(pg_connection, exercise_clock) -> CampaignStateStore:
    return CampaignStateStore(pg_connection, clock=exercise_clock)


@pytest.fixture
def running_exercise_id(exercise_store: ExerciseStore) -> str:
    return exercise_store.start(scenario(), roster()).exercise_id


class TestStartInsertsTheDefaultRow:
    def test_a_freshly_started_exercise_already_has_campaign_state(
        self, exercise_store: ExerciseStore, running_exercise_id: str
    ) -> None:
        current = exercise_store.current()
        assert current is not None
        assert current.campaign is not None
        assert current.campaign.phase == "briefing"
        assert current.campaign.chapter is None
        assert current.campaign.paused is False

    def test_get_returns_the_same_default_state(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        state = campaign_store.get(running_exercise_id)
        assert state is not None
        assert state.phase == "briefing"


class TestAdvancePhase:
    def test_advance_sets_phase_and_chapter(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        state = campaign_store.advance_phase(
            running_exercise_id, phase="initial", chapter="CH1"
        )
        assert state.phase == "initial"
        assert state.chapter == "CH1"

    def test_omitted_chapter_keeps_the_previously_set_one(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        campaign_store.advance_phase(running_exercise_id, phase="initial", chapter="CH1")

        state = campaign_store.advance_phase(running_exercise_id, phase="escalation")

        assert state.chapter == "CH1"
        assert state.phase == "escalation"

    def test_unknown_phase_is_rejected(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        with pytest.raises(ValueError):
            campaign_store.advance_phase(running_exercise_id, phase="not-a-real-phase")

    def test_unknown_exercise_id_raises_not_found(
        self, campaign_store: CampaignStateStore
    ) -> None:
        with pytest.raises(CampaignStateNotFound):
            campaign_store.advance_phase("ex-does-not-exist", phase="initial")


class TestBgm:
    def test_set_bgm_does_not_touch_phase_or_chapter(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        campaign_store.advance_phase(running_exercise_id, phase="critical", chapter="CH4")

        state = campaign_store.set_bgm(running_exercise_id, "critical")

        assert state.bgm_phase == "critical"
        assert state.phase == "critical"
        assert state.chapter == "CH4"


class TestPauseAndResume:
    def test_pause_sets_paused_and_paused_at(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        state = campaign_store.pause(running_exercise_id)
        assert state.paused is True
        assert state.paused_at is not None

    def test_pausing_twice_is_rejected(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        campaign_store.pause(running_exercise_id)
        with pytest.raises(AlreadyPaused):
            campaign_store.pause(running_exercise_id)

    def test_resuming_without_a_pause_is_rejected(
        self, campaign_store: CampaignStateStore, running_exercise_id: str
    ) -> None:
        with pytest.raises(NotPaused):
            campaign_store.resume(running_exercise_id)

    def test_resume_shifts_ends_at_forward_by_the_pause_duration(
        self,
        exercise_store: ExerciseStore,
        campaign_store: CampaignStateStore,
        running_exercise_id: str,
        exercise_clock,
    ) -> None:
        before = exercise_store.current()
        assert before is not None
        original_ends_at = before.ends_at

        campaign_store.pause(running_exercise_id)
        exercise_clock.advance(timedelta(minutes=5))

        state = campaign_store.resume(running_exercise_id)
        assert state.paused is False
        assert state.paused_at is None

        after = exercise_store.current()
        assert after is not None
        assert after.ends_at == original_ends_at + timedelta(minutes=5)

    def test_resume_never_leaves_ends_at_shorter_than_before(
        self,
        exercise_store: ExerciseStore,
        campaign_store: CampaignStateStore,
        running_exercise_id: str,
        exercise_clock,
    ) -> None:
        """A paused campaign must not eat into the room's actual playtime --
        a zero-second pause is the floor, never a net loss."""
        before = exercise_store.current()
        assert before is not None

        campaign_store.pause(running_exercise_id)
        campaign_store.resume(running_exercise_id)

        after = exercise_store.current()
        assert after is not None
        assert after.ends_at >= before.ends_at


def test_truncate_all_clears_campaign_state_between_tests(
    exercise_store: ExerciseStore, campaign_store: CampaignStateStore
) -> None:
    """`conftest.py`'s `exercise_store` fixture calls `truncate_all` before
    every test -- if `exercise_campaign_state` were left out of that list,
    this would fail with a stale row from a previous test instead of a
    clean 'no exercise yet' state."""
    assert exercise_store.current() is None
    assert campaign_store.get("ex-anything") is None
