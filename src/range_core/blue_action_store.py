"""Blue Action ingest — the I/O shell around `blue_actions.py`'s pure domain
rules (#36 Phase 2, WS3 spec §4/§5, WS5 spec §1.2).

`BlueActionLog`/`build_action` (in `blue_actions.py`) already decide *what's
legal*: closed enumeration, one event_id per action, one judgement per event.
This module is the thin layer that makes those decisions stick against real
storage — same split as `objectives.py`'s pure completion rules vs.
`ObjectiveStore`'s I/O.

Two checks exist at both the application layer and the database layer,
deliberately: `build_action` validates shape before any query runs (fail
fast, no wasted round trip); `blue_actions_one_judgement_idx` (a partial
unique index, same technique as `exercises_one_running_idx`) is what actually
makes "one shot" true under concurrent submissions — two simultaneous
`classify` calls for the same event race at the application layer's
in-memory check, but the database only lets one through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from disclosure import DETECTION_EVENT_TYPES

from range_core.blue_actions import (
    BlueAction,
    BlueActionLog,
    BlueActionRejected,
    BlueActionType,
    build_action,
)
from range_core.objectives import Clock, SystemClock


class UnknownEvent(BlueActionRejected):
    """`event_id` has no detection Core Event on record for this exercise.

    A Blue Action must point at something that actually happened — accepting
    one against an unknown `event_id` would let a client fabricate a reaction
    time against an event that was never detected.
    """


class AlreadyJudged(BlueActionRejected):
    """The database's `blue_actions_one_judgement_idx` fired.

    Distinct from the base `BlueActionRejected` (shape/validation failures
    from `build_action`, e.g. an unknown action or a `classify` missing its
    technique) so the API layer can tell "malformed request" (400) apart
    from "this specific event already got its one shot" (409) without
    string-matching an error message.
    """


@dataclass(frozen=True)
class BlueActionStore:
    """Writes and reads `exercise_blue_actions`."""

    conn: psycopg.Connection
    clock: Clock = SystemClock()

    def record(
        self, exercise_id: str, action: str, event_id: str, technique: str | None = None
    ) -> BlueAction:
        """Validate, then persist. Raises `BlueActionRejected` (or the
        `UnknownEvent` subclass) with the same messages the pure domain
        layer would give — the API layer doesn't need two vocabularies for
        the same kind of failure.
        """
        parsed = build_action(action, event_id, self.clock.now(), technique)

        if not self._event_exists(exercise_id, event_id):
            raise UnknownEvent(
                f"event {event_id!r} has no detection Core Event on record for this exercise"
            )

        try:
            self.conn.execute(
                """
                INSERT INTO exercise_blue_actions
                    (exercise_id, event_id, action, submitted_at, technique)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (exercise_id, parsed.event_id, parsed.action.value, parsed.submitted_at, parsed.technique),
            )
        except psycopg.errors.UniqueViolation as exc:
            # `blue_actions_one_judgement_idx` fired: someone already
            # classified/dismissed this event. Same message shape as
            # `BlueActionLog.record`'s in-memory check, so callers don't
            # need to know which layer caught the race.
            raise AlreadyJudged(
                f"event {event_id!r} was already judged; "
                f"classify/dismiss is one shot per event (WS3 spec §4.2)"
            ) from exc

        return parsed

    def _event_exists(self, exercise_id: str, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM core_events "
            "WHERE exercise_id = %s AND event_id = %s AND lifecycle = 'firing' "
            "AND event_type = ANY(%s)",
            (exercise_id, event_id, list(DETECTION_EVENT_TYPES)),
        ).fetchone()
        return row is not None

    def for_exercise(self, exercise_id: str) -> BlueActionLog:
        """The whole exercise's actions, as the pure domain type.

        Rebuilding via `BlueActionLog(...)` (which calls `.record()` for
        each row) re-runs the one-shot check on load — a second, independent
        confirmation that stored data still honors the invariant, on top of
        the database constraint that put it there.
        """
        rows = self.conn.execute(
            """
            SELECT event_id, action, submitted_at, technique
            FROM exercise_blue_actions
            WHERE exercise_id = %s
            ORDER BY submitted_at, id
            """,
            (exercise_id,),
        ).fetchall()
        return BlueActionLog(
            BlueAction(
                action=BlueActionType(row[1]),
                event_id=row[0],
                submitted_at=row[2],
                technique=row[3],
            )
            for row in rows
        )

    def observed_at_by_event(self, exercise_id: str) -> dict[str, datetime]:
        """`event_id -> observed_at` for every Core Event this exercise's
        Blue Actions could reference — the shared clock origin both
        `Detect Attack` and `Contain < 60 sec` measure from (WS3 §4.5).

        **`firing` only.** A `resolved` row for the same `event_id` can carry
        a *different* `observed_at` (when it resolved, not when it fired);
        without this filter a resolved detection would produce two rows for
        one `event_id` and which `observed_at` wins would depend on row
        order. The reaction-time origin is always "when it fired."
        """
        rows = self.conn.execute(
            "SELECT event_id, observed_at FROM core_events "
            "WHERE exercise_id = %s AND lifecycle = 'firing' AND event_type = ANY(%s)",
            (exercise_id, list(DETECTION_EVENT_TYPES)),
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def technique_by_event(self, exercise_id: str) -> dict[str, str]:
        """`event_id -> technique`, read from the stored Core Event JSON.

        This is the platform's own record of the correct answer (WS3 §2.1):
        it is never masked here — masking only happens on the *outbound*
        projection to red/blue (`disclosure.project_fields`). Scoring reads
        the real value directly, same as it must to grade anything.
        """
        rows = self.conn.execute(
            "SELECT event_id, event FROM core_events WHERE exercise_id = %s "
            "AND lifecycle = 'firing' AND event_type = ANY(%s)",
            (exercise_id, list(DETECTION_EVENT_TYPES)),
        ).fetchall()
        result: dict[str, str] = {}
        for event_id, event in rows:
            technique = _event_technique(event)
            if technique is not None:
                result[event_id] = technique
        return result


def _event_technique(event: Any) -> str | None:
    value = event.get("technique") if isinstance(event, dict) else None
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class StoredExecutionEvidence:
    """Independent ground truth used to adjudicate ``dismiss``.

    ``action_executions`` is written by the attack harness, independently of
    Grafana's detection decision.  Joining through the Core Event's explicit
    ``action_id`` therefore answers "was the triggering action really
    executed?" without consulting coverage, MTTR, containment duration, or
    any other P2 metric.  A missing relation is a false-positive candidate,
    not proof manufactured from the detection itself (WS3 spec §4.1).
    """

    conn: psycopg.Connection
    exercise_id: str

    def has_evidence(self, event_id: str) -> bool:
        return bool(
            self.conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM core_events AS event
                    JOIN action_executions AS execution
                      ON execution.exercise_id = event.exercise_id
                     AND execution.action_id = event.action_id
                    WHERE event.exercise_id = %s AND event.event_id = %s
                )
                """,
                (self.exercise_id, event_id),
            ).fetchone()[0]
        )
