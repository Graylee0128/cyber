from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from range_core.exercises import (
    ExerciseAlreadyRunning,
    ExerciseStore,
    PlayerRegistration,
)
from range_core.scenarios import Scenario


def scenario(duration: str = "30m") -> Scenario:
    return Scenario.model_validate(
        {
            "id": "sqli-01",
            "name": "SQL Injection",
            "difficulty": "easy",
            "duration": duration,
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
        }
    )


def roster() -> tuple[PlayerRegistration, ...]:
    return (PlayerRegistration(player_id="red-alice", source_ip="10.30.0.11"),)


def test_start_exercise_registers_roster_and_becomes_current(
    exercise_store: ExerciseStore,
) -> None:
    players = (
        PlayerRegistration(player_id="red-alice", source_ip="10.30.0.11"),
        PlayerRegistration(player_id="red-bob", source_ip="10.30.0.16"),
    )

    started = exercise_store.start(scenario(), players)

    assert exercise_store.current() == started
    assert started.scenario_id == "sqli-01"
    assert started.state == "running"
    assert started.started_at == datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
    assert started.ends_at == datetime(2026, 8, 11, 8, 30, tzinfo=timezone.utc)
    assert started.players == players


def test_exercise_ends_only_when_scenario_duration_elapses(
    exercise_store: ExerciseStore,
    exercise_clock,
) -> None:
    started = exercise_store.start(scenario(), roster())

    exercise_clock.advance(timedelta(minutes=29, seconds=59))
    assert exercise_store.current() == started

    exercise_clock.advance(timedelta(seconds=1))
    assert exercise_store.current() is None
    ended = exercise_store.get(started.exercise_id)
    assert ended is not None
    assert ended.state == "ended"
    assert ended.ended_at == started.ends_at


def test_database_rejects_a_second_running_exercise(
    exercise_store: ExerciseStore,
) -> None:
    exercise_store.start(scenario(), roster())

    with pytest.raises(ExerciseAlreadyRunning, match="already running"):
        exercise_store.start(scenario(), roster())


def test_every_roster_row_keeps_its_exercise_id(
    pg_connection,
    exercise_store: ExerciseStore,
) -> None:
    started = exercise_store.start(
        scenario(),
        (
            PlayerRegistration(player_id="red-alice", source_ip="10.30.0.11"),
            PlayerRegistration(player_id="red-bob", source_ip="10.30.0.12"),
        ),
    )

    exercise_ids = pg_connection.execute(
        "SELECT exercise_id FROM exercise_players ORDER BY player_id"
    ).fetchall()
    assert exercise_ids == [(started.exercise_id,), (started.exercise_id,)]


def test_roster_rejects_source_ip_outside_the_six_kali_hosts() -> None:
    with pytest.raises(ValueError, match=r"\.11 through \.16"):
        PlayerRegistration(player_id="red-seven", source_ip="10.30.0.17")
