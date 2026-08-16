"""Campaign/Experience Layer state persistence (#153).

`exercise_campaign_state` (schema in `exercises.py`) holds the one thing the
Experience Layer needs that nothing else in `range_core` tracks: which
Chapter/phase the room is currently in, what BGM cue is active, and whether
the clock is paused. `ExerciseStore` inserts the default row alongside every
`exercises` row (`start`/`start_prepared`) and reads it back in `_hydrate`
via the read-only `CampaignState` projection (`exercises.py`) -- this module
is the one place that *writes* it.

Same split as `BlueActionStore`/`ObjectiveStore`: this is the I/O shell, not
domain rules -- there isn't much domain logic here (advance/pause/resume are
each one straightforward state change), so unlike `blue_actions.py` there is
no separate pure-domain module to wrap.

Deliberately does not touch `core_events`: emitting the `campaign.*` Core
Event that announces a phase/announcement change is `campaign_events.py`'s
job (#153 PR 3), called by the API layer alongside (not inside) these
methods. Pause/resume never emit an event at all -- the contract has no cue
for them, they only affect `ends_at`/`countdown()`.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from range_core.exercises import CampaignState, Clock, SystemClock


class CampaignStateNotFound(LookupError):
    """No `exercise_campaign_state` row for this `exercise_id`.

    Should not happen for any exercise started after #153 shipped (`start`/
    `start_prepared` always insert the row in the same transaction) -- this
    exists for exercises started before the migration, and to fail loudly
    rather than silently no-op if that assumption is ever wrong.
    """


class AlreadyPaused(RuntimeError):
    """`pause()` called on a campaign that is already paused."""


class NotPaused(RuntimeError):
    """`resume()` called on a campaign that is not currently paused."""


_PHASES = frozenset(
    {"briefing", "initial", "escalation", "critical", "final", "debrief"}
)


@dataclass(frozen=True)
class CampaignStateStore:
    """Reads and writes `exercise_campaign_state`."""

    conn: psycopg.Connection
    clock: Clock = SystemClock()

    def get(self, exercise_id: str) -> CampaignState | None:
        row = self.conn.execute(
            """
            SELECT chapter, phase, bgm_phase, paused, paused_at
            FROM exercise_campaign_state WHERE exercise_id = %s
            """,
            (exercise_id,),
        ).fetchone()
        return _hydrate(row) if row is not None else None

    def advance_phase(
        self,
        exercise_id: str,
        *,
        phase: str,
        chapter: str | None = None,
        bgm_phase: str | None = None,
    ) -> CampaignState:
        """Reveal/advance Chapter, Final countdown, End/result reveal --
        all the same operation with a different `phase` value (experience-
        contract.md's Instructor-as-GM MVP list). `chapter`/`bgm_phase`
        left unset (`None`) keep their current stored value; there is no
        way to *clear* an already-set chapter through this call, matching
        the campaign's one-directional "story only moves forward" shape.
        """
        if phase not in _PHASES:
            raise ValueError(f"unknown phase: {phase!r}")
        row = self.conn.execute(
            """
            UPDATE exercise_campaign_state
            SET phase = %s,
                chapter = COALESCE(%s, chapter),
                bgm_phase = COALESCE(%s, bgm_phase),
                updated_at = %s
            WHERE exercise_id = %s
            RETURNING chapter, phase, bgm_phase, paused, paused_at
            """,
            (phase, chapter, bgm_phase, self.clock.now(), exercise_id),
        ).fetchone()
        if row is None:
            raise CampaignStateNotFound(exercise_id)
        return _hydrate(row)

    def set_bgm(self, exercise_id: str, bgm_phase: str) -> CampaignState:
        """Standalone BGM switch not tied to a phase change. Deliberately
        produces no Core Event (experience-contract.md: BGM switching is
        manual and non-reactive by design) -- callers see it only via the
        next `GET /api/exercises/current` poll."""
        row = self.conn.execute(
            """
            UPDATE exercise_campaign_state
            SET bgm_phase = %s, updated_at = %s
            WHERE exercise_id = %s
            RETURNING chapter, phase, bgm_phase, paused, paused_at
            """,
            (bgm_phase, self.clock.now(), exercise_id),
        ).fetchone()
        if row is None:
            raise CampaignStateNotFound(exercise_id)
        return _hydrate(row)

    def pause(self, exercise_id: str) -> CampaignState:
        now = self.clock.now()
        with self.conn.transaction():
            current = self.conn.execute(
                "SELECT paused FROM exercise_campaign_state WHERE exercise_id = %s FOR UPDATE",
                (exercise_id,),
            ).fetchone()
            if current is None:
                raise CampaignStateNotFound(exercise_id)
            if current[0]:
                raise AlreadyPaused(exercise_id)
            row = self.conn.execute(
                """
                UPDATE exercise_campaign_state
                SET paused = true, paused_at = %s, updated_at = %s
                WHERE exercise_id = %s
                RETURNING chapter, phase, bgm_phase, paused, paused_at
                """,
                (now, now, exercise_id),
            ).fetchone()
        return _hydrate(row)

    def resume(self, exercise_id: str) -> CampaignState:
        """Un-pauses and shifts `exercises.ends_at` forward by however long
        the pause lasted, so a paused campaign never eats into the room's
        actual playtime. Both writes happen in one transaction -- a partial
        resume (state flips but the clock doesn't move, or vice versa)
        would be a worse failure mode than the whole call failing."""
        now = self.clock.now()
        with self.conn.transaction():
            current = self.conn.execute(
                "SELECT paused, paused_at FROM exercise_campaign_state WHERE exercise_id = %s FOR UPDATE",
                (exercise_id,),
            ).fetchone()
            if current is None:
                raise CampaignStateNotFound(exercise_id)
            paused, paused_at = current
            if not paused:
                raise NotPaused(exercise_id)
            elapsed_s = int((now - paused_at).total_seconds())
            self.conn.execute(
                "UPDATE exercises SET ends_at = ends_at + (%s * interval '1 second') WHERE exercise_id = %s",
                (elapsed_s, exercise_id),
            )
            row = self.conn.execute(
                """
                UPDATE exercise_campaign_state
                SET paused = false, paused_at = NULL,
                    pause_accumulated_s = pause_accumulated_s + %s,
                    updated_at = %s
                WHERE exercise_id = %s
                RETURNING chapter, phase, bgm_phase, paused, paused_at
                """,
                (elapsed_s, now, exercise_id),
            ).fetchone()
        return _hydrate(row)


def _hydrate(row: tuple) -> CampaignState:
    return CampaignState(
        chapter=row[0], phase=row[1], bgm_phase=row[2], paused=row[3], paused_at=row[4]
    )
