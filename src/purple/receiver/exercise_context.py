"""Read the active WS5 exercise context at the receiver seam.

The receiver does not trust Grafana-provided exercise labels. PostgreSQL's
single running exercise is the authority, read through this small published
table contract without importing Range Core's domain package into ``purple``.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


class NoRunningExercise(RuntimeError):
    """A detection cannot be attributed because no exercise is active."""


@dataclass(frozen=True)
class ExerciseContext:
    exercise_id: str
    scenario_id: str


@dataclass
class RunningExerciseLookup:
    conn: psycopg.Connection

    def require(self) -> ExerciseContext:
        row = self.conn.execute(
            """
            SELECT exercise_id, scenario_id
            FROM exercises
            WHERE state = 'running' AND ends_at > now()
            """
        ).fetchone()
        if row is None:
            raise NoRunningExercise("no exercise is currently running")
        return ExerciseContext(exercise_id=row[0], scenario_id=row[1])
