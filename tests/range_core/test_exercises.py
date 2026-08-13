from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

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
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "attack_chain": [
                {
                    "id": "exploit-web",
                    "technique": "T1190",
                    "description": "Exploit the target.",
                }
            ],
            "reset_scope": "exercise",
        }
    )


def roster() -> tuple[PlayerRegistration, ...]:
    return (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),)


def test_start_exercise_registers_roster_and_becomes_current(
    exercise_store: ExerciseStore,
) -> None:
    players = (
        PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),
        PlayerRegistration(player_id="red-bob", source_ip="10.167.30.16"),
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
            PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),
            PlayerRegistration(player_id="red-bob", source_ip="10.167.30.12"),
        ),
    )

    exercise_ids = pg_connection.execute(
        "SELECT exercise_id FROM exercise_players ORDER BY player_id"
    ).fetchall()
    assert exercise_ids == [(started.exercise_id,), (started.exercise_id,)]


def test_roster_accepts_scaled_seats_and_rejects_non_seat_addresses() -> None:
    assert PlayerRegistration(
        player_id="red-sixty", source_ip="10.167.30.60"
    ).source_ip == "10.167.30.60"

    for source_ip in (
        "10.167.30.0",  # network
        "10.167.30.1",  # gateway
        "10.167.30.10",  # reserved/control range
        "10.167.30.255",  # broadcast
        "10.167.60.60",  # Z-BLUE, not Z-RED
        "192.0.2.60",  # unrelated zone
    ):
        with pytest.raises(ValueError, match=r"10\.167\.30\.11 through \.254"):
            PlayerRegistration(player_id="not-a-red-seat", source_ip=source_ip)


def test_reset_deletes_exercise_scoped_state_and_allows_immediate_restart(
    pg_connection,
    exercise_store: ExerciseStore,
) -> None:
    first = exercise_store.start(scenario(), roster())
    pg_connection.execute(
        """
        INSERT INTO exercise_objective_completions
            (exercise_id, player_id, objective_id, completed_at, evaluation, evidence_event_id)
        VALUES (%s, 'red-alice', 'capture_flag', now(), 'submission', NULL)
        """,
        (first.exercise_id,),
    )
    pg_connection.execute(
        """
        INSERT INTO exercise_hint_usages
            (exercise_id, player_id, objective_id, hint_index, used_at)
        VALUES (%s, 'red-alice', 'capture_flag', 0, now())
        """,
        (first.exercise_id,),
    )
    pg_connection.execute(
        """
        INSERT INTO core_events
            (event_id, lifecycle, event_type, exercise_id, scenario_id,
             observed_at, event)
        VALUES
            ('evt-reset-audit', 'firing', 'attack.detected', %s, 'sqli-01',
             now(), '{}'::jsonb)
        """,
        (first.exercise_id,),
    )

    reset_started = perf_counter()
    assert exercise_store.reset_current() == first.exercise_id
    reset_elapsed = perf_counter() - reset_started

    assert exercise_store.current() is None
    assert exercise_store.get(first.exercise_id) is None
    assert pg_connection.execute(
        "SELECT count(*) FROM exercise_objective_completions"
    ).fetchone() == (0,)
    assert pg_connection.execute(
        "SELECT count(*) FROM exercise_hint_usages"
    ).fetchone() == (0,)
    assert pg_connection.execute(
        "SELECT exercise_id FROM core_events WHERE event_id = 'evt-reset-audit'"
    ).fetchone() == (first.exercise_id,)
    assert reset_elapsed < 1.0
    assert exercise_store.start(scenario(), roster()).state == "running"


def test_exercise_schema_has_no_writable_score_column(pg_connection) -> None:
    columns = pg_connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name LIKE 'exercise%'
          AND column_name = 'score'
        """
    ).fetchall()

    assert columns == []


def test_no_table_in_the_database_has_a_score_column(pg_connection) -> None:
    """#33: score is derived at read time, never stored, anywhere — not
    just on exercise-prefixed tables. A future table named something else
    entirely must not quietly reintroduce a stored score."""
    columns = pg_connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'score'
        """
    ).fetchall()

    assert columns == []


def test_reset_implementation_has_no_range_script_or_process_boundary() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "src" / "range_core" / "exercises.py"
    ).read_text(encoding="utf-8")

    assert "scripts/range" not in source
    assert "subprocess" not in source
    assert "os.system" not in source
