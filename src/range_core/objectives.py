"""Objective completion (telemetry + submission) and hint usage (#33).

Both completion paths funnel through `ObjectiveStore`, which is the only
writer of `exercise_objective_completions` — it is what keeps the
`evaluation`/`evidence_event_id` pairing (schema CHECK in `exercises.py`)
honest: the telemetry path always supplies an `event_id`, the submission
path never does.

Player identity is resolved from source IP against the exercise's roster
(`exercise_players`, owned by #32) — there is no separate login system
(WS5-4 spec). `PlayerLookup.player_for` returning `None` means "this
source IP isn't on the roster", and callers must treat that as a 403, never
as "fall back to the only registered player".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

import psycopg

from range_core.scenarios import Objective, Scenario


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ObjectiveCompletion:
    exercise_id: str
    player_id: str
    objective_id: str
    completed_at: datetime
    evaluation: Literal["telemetry", "submission"]
    evidence_event_id: str | None


@dataclass(frozen=True)
class HintUsage:
    exercise_id: str
    player_id: str
    objective_id: str
    hint_index: int
    used_at: datetime


class UnknownObjective(ValueError):
    """The scenario has no objective with this id."""


class ObjectiveEvaluationMismatch(ValueError):
    """Caller used the wrong completion path for this objective's `evaluation`."""


class HintIndexOutOfRange(ValueError):
    """No hint at this index for this objective."""


@dataclass(frozen=True)
class PlayerLookup:
    """Source IP -> player_id, scoped to one exercise's roster (#32)."""

    conn: psycopg.Connection

    def player_for(self, exercise_id: str, source_ip: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT player_id FROM exercise_players
            WHERE exercise_id = %s AND source_ip = %s::inet AND active
            """,
            (exercise_id, source_ip),
        ).fetchone()
        return row[0] if row is not None else None


@dataclass(frozen=True)
class ObjectiveStore:
    """Writes and reads `exercise_objective_completions`."""

    conn: psycopg.Connection
    clock: Clock = SystemClock()

    def complete_by_telemetry(
        self,
        exercise_id: str,
        player_id: str,
        objective_id: str,
        event_id: str,
        *,
        objective: Objective,
    ) -> bool:
        """Idempotent: `ON CONFLICT DO NOTHING` on the existing PK, so a
        repeat trigger for an already-completed objective is a no-op and
        `completed_at` never moves."""
        if objective.evaluation != "telemetry":
            raise ObjectiveEvaluationMismatch(
                f"objective {objective_id!r} is {objective.evaluation!r}, not telemetry"
            )
        return self._insert(
            exercise_id, player_id, objective_id, evaluation="telemetry", evidence_event_id=event_id
        )

    def complete_by_submission(
        self,
        exercise_id: str,
        player_id: str,
        objective_id: str,
        *,
        objective: Objective,
    ) -> bool:
        if objective.evaluation != "submission":
            raise ObjectiveEvaluationMismatch(
                f"objective {objective_id!r} is {objective.evaluation!r}, not submission"
            )
        return self._insert(
            exercise_id, player_id, objective_id, evaluation="submission", evidence_event_id=None
        )

    def _insert(
        self,
        exercise_id: str,
        player_id: str,
        objective_id: str,
        *,
        evaluation: str,
        evidence_event_id: str | None,
    ) -> bool:
        row = self.conn.execute(
            """
            INSERT INTO exercise_objective_completions
                (exercise_id, player_id, objective_id, completed_at, evaluation, evidence_event_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (exercise_id, player_id, objective_id) DO NOTHING
            RETURNING objective_id
            """,
            (exercise_id, player_id, objective_id, self.clock.now(), evaluation, evidence_event_id),
        ).fetchone()
        return row is not None

    def for_exercise(self, exercise_id: str) -> tuple[ObjectiveCompletion, ...]:
        rows = self.conn.execute(
            """
            SELECT exercise_id, player_id, objective_id, completed_at, evaluation, evidence_event_id
            FROM exercise_objective_completions
            WHERE exercise_id = %s
            ORDER BY completed_at
            """,
            (exercise_id,),
        ).fetchall()
        return tuple(
            ObjectiveCompletion(
                exercise_id=row[0],
                player_id=row[1],
                objective_id=row[2],
                completed_at=row[3],
                evaluation=row[4],
                evidence_event_id=row[5],
            )
            for row in rows
        )


@dataclass(frozen=True)
class HintService:
    """Records hint requests; never returns text through a diagnostics or
    listing path (that would let anyone see hints for free — the pre-scoring
    prerequisite noted during #33 review). Only `request(...)` returns text,
    and only after recording the usage."""

    conn: psycopg.Connection
    clock: Clock = SystemClock()

    def penalties_for(self, scenario: Scenario, objective_id: str) -> tuple[dict, ...]:
        """`[{index, penalty_percent}]` — no `text`. Lets the player see the
        cost before confirming, per product-ui decision, without leaking
        the hint itself."""
        return tuple(
            {"index": index, "penalty_percent": hint.penalty_percent}
            for index, hint in enumerate(scenario.hints_for(objective_id))
        )

    def request(
        self, scenario: Scenario, exercise_id: str, player_id: str, objective_id: str, hint_index: int
    ) -> str:
        """Records the usage (idempotent — a repeat request for an index
        already used does not create a second row and does not double the
        penalty) and returns the hint text."""
        hints = scenario.hints_for(objective_id)
        if not (0 <= hint_index < len(hints)):
            raise HintIndexOutOfRange(
                f"objective {objective_id!r} has no hint at index {hint_index}"
            )
        self.conn.execute(
            """
            INSERT INTO exercise_hint_usages
                (exercise_id, player_id, objective_id, hint_index, used_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (exercise_id, player_id, objective_id, hint_index) DO NOTHING
            """,
            (exercise_id, player_id, objective_id, hint_index, self.clock.now()),
        )
        return hints[hint_index].text

    def for_exercise(self, exercise_id: str) -> tuple[HintUsage, ...]:
        rows = self.conn.execute(
            """
            SELECT exercise_id, player_id, objective_id, hint_index, used_at
            FROM exercise_hint_usages
            WHERE exercise_id = %s
            ORDER BY used_at
            """,
            (exercise_id,),
        ).fetchall()
        return tuple(
            HintUsage(
                exercise_id=row[0],
                player_id=row[1],
                objective_id=row[2],
                hint_index=row[3],
                used_at=row[4],
            )
            for row in rows
        )
