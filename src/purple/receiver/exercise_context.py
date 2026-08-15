"""Read the active WS5 exercise identifier at the receiver seam.

The receiver does not trust Grafana-provided exercise labels. PostgreSQL's
single running exercise is the authority, read through this small published
table contract without importing Range Core's domain package into ``purple``.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg


class NoRunningExercise(RuntimeError):
    """A detection cannot be attributed because no exercise is active."""


@dataclass
class RunningExerciseLookup:
    conn: psycopg.Connection

    def require_id(self) -> str:
        row = self.conn.execute(
            """
            SELECT exercise_id
            FROM exercises
            WHERE state = 'running' AND ends_at > now()
            """
        ).fetchone()
        if row is None:
            raise NoRunningExercise("no exercise is currently running")
        return row[0]
