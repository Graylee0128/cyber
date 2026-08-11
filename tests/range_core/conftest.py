from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from range_core.exercises import ExerciseStore, ensure_schema, truncate_all


class FixedClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


@pytest.fixture
def exercise_clock() -> FixedClock:
    return FixedClock(datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc))


@pytest.fixture
def exercise_store(pg_connection, exercise_clock: FixedClock) -> ExerciseStore:
    ensure_schema(pg_connection)
    truncate_all(pg_connection)
    return ExerciseStore(
        pg_connection,
        clock=exercise_clock,
    )
