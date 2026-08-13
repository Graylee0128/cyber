"""Exercise lifecycle persistence for Cyber Range Core.

The schema lives in :mod:`range_core`, not ``purple``: exercises are Z-APP
domain data even though the receiver reads the current exercise identifier as
a published database contract.
"""

from __future__ import annotations

import ipaddress
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

import psycopg
from pydantic import BaseModel, ConfigDict, Field, field_validator

from range_core.scenarios import Scenario


SCHEMA = """
CREATE TABLE IF NOT EXISTS exercises (
    exercise_id text        PRIMARY KEY,
    scenario_id text        NOT NULL,
    state       text        NOT NULL CHECK (state IN ('running', 'ended')),
    started_at  timestamptz NOT NULL,
    ends_at     timestamptz NOT NULL,
    ended_at    timestamptz,
    CHECK (ends_at > started_at),
    CHECK ((state = 'running' AND ended_at IS NULL)
        OR (state = 'ended' AND ended_at IS NOT NULL))
);

-- The database, not application pre-checks, is the single-running authority.
CREATE UNIQUE INDEX IF NOT EXISTS exercises_one_running_idx
    ON exercises ((state))
    WHERE state = 'running';

CREATE TABLE IF NOT EXISTS exercise_players (
    exercise_id text NOT NULL REFERENCES exercises(exercise_id) ON DELETE CASCADE,
    player_id   text NOT NULL,
    source_ip   inet NOT NULL,
    PRIMARY KEY (exercise_id, player_id),
    UNIQUE (exercise_id, source_ip)
);

-- #33 owns the completion and scoring behavior.  #32 owns their lifecycle:
-- every derived row is exercise-scoped and disappears with its exercise.
--
-- `evidence_event_id` has no FK to core_events: that table's PK is
-- (event_id, lifecycle), and #32 deliberately keeps it outside this
-- aggregate's cascade so reset cannot erase the audit trail. The CHECK
-- below is the only enforcement that a telemetry completion carries
-- evidence and a submission completion does not.
CREATE TABLE IF NOT EXISTS exercise_objective_completions (
    exercise_id text        NOT NULL,
    player_id   text        NOT NULL,
    objective_id text       NOT NULL,
    completed_at timestamptz NOT NULL,
    evaluation  text,
    evidence_event_id text,
    PRIMARY KEY (exercise_id, player_id, objective_id),
    FOREIGN KEY (exercise_id, player_id)
        REFERENCES exercise_players(exercise_id, player_id) ON DELETE CASCADE
);

-- Existing-database upgrade path (#32 shipped this table without these
-- columns): CREATE TABLE IF NOT EXISTS above is a no-op once the table
-- already exists, so add the columns explicitly. Backfill before the
-- NOT NULL so a dirty pre-existing table can't fail the migration.
ALTER TABLE exercise_objective_completions ADD COLUMN IF NOT EXISTS evaluation text;
ALTER TABLE exercise_objective_completions ADD COLUMN IF NOT EXISTS evidence_event_id text;
UPDATE exercise_objective_completions SET evaluation = 'submission' WHERE evaluation IS NULL;
ALTER TABLE exercise_objective_completions ALTER COLUMN evaluation SET NOT NULL;
ALTER TABLE exercise_objective_completions DROP CONSTRAINT IF EXISTS exercise_objective_completions_evaluation_check;
ALTER TABLE exercise_objective_completions ADD CONSTRAINT exercise_objective_completions_evaluation_check
    CHECK (evaluation IN ('telemetry', 'submission'));
ALTER TABLE exercise_objective_completions DROP CONSTRAINT IF EXISTS completion_evidence_matches_evaluation;
ALTER TABLE exercise_objective_completions ADD CONSTRAINT completion_evidence_matches_evaluation CHECK (
    (evaluation = 'telemetry'  AND evidence_event_id IS NOT NULL) OR
    (evaluation = 'submission' AND evidence_event_id IS NULL)
);

CREATE TABLE IF NOT EXISTS exercise_hint_usages (
    exercise_id text        NOT NULL,
    player_id   text        NOT NULL,
    objective_id text       NOT NULL,
    hint_index  integer     NOT NULL CHECK (hint_index >= 0),
    used_at     timestamptz NOT NULL,
    PRIMARY KEY (exercise_id, player_id, objective_id, hint_index),
    FOREIGN KEY (exercise_id, player_id)
        REFERENCES exercise_players(exercise_id, player_id) ON DELETE CASCADE
);

-- Blue Action ingest (#36 Phase 2, WS3 spec §4/§5). No player_id: "who" is
-- always the team `blue` (WS3 §5.1, Blue is not individualized). This is an
-- append-only audit input to Event Service, not mutable Exercise aggregate
-- state, so it deliberately has no cascading FK: reset removes the live
-- score/lifecycle aggregate but must not erase the action trace needed for
-- replay and post-exercise audit. BlueActionStore validates the exercise and
-- referenced Core Event before writing.
CREATE TABLE IF NOT EXISTS exercise_blue_actions (
    id           bigserial   PRIMARY KEY,
    exercise_id  text        NOT NULL,
    event_id     text        NOT NULL,
    action       text        NOT NULL
        CHECK (action IN ('acknowledge', 'classify', 'contain', 'resolve', 'dismiss')),
    submitted_at timestamptz NOT NULL,
    technique    text,
    recorded_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS blue_actions_exercise_event_idx
    ON exercise_blue_actions (exercise_id, event_id);

-- 判讀一次定生死（WS3 spec §4.2）：DB 是唯一權威，不只靠應用層檢查 ——
-- 比照 `exercises_one_running_idx` 已經在用的同一個手法（partial unique index）。
CREATE UNIQUE INDEX IF NOT EXISTS blue_actions_one_judgement_idx
    ON exercise_blue_actions (exercise_id, event_id)
    WHERE action IN ('classify', 'dismiss');
"""

SCHEMA_LOCK_KEY = 0x52414E47  # "RANG"
DEFAULT_DSN = "postgresql://purple:purple@localhost:5432/purple"
_DURATION = re.compile(r"^(?P<amount>[1-9][0-9]*)(?P<unit>[smhd])$")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
KALI_ADDRESSES = frozenset(
    ipaddress.ip_address(f"10.167.30.{last_octet}") for last_octet in range(11, 17)
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ExerciseAlreadyRunning(RuntimeError):
    """PostgreSQL rejected a second row covered by the running-only index."""


class PlayerRegistration(BaseModel):
    """One Red player bound to one of the six Kali source addresses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    player_id: str = Field(min_length=1)
    source_ip: str

    @field_validator("source_ip")
    @classmethod
    def source_is_a_kali_address(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("source_ip must be an IPv4 address") from exc
        if address not in KALI_ADDRESSES:
            raise ValueError("source_ip must be a Z-RED Kali address 10.167.30.11 through .16")
        return str(address)


class Exercise(BaseModel):
    """One immutable view of an exercise and its exercise-scoped roster."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exercise_id: str
    scenario_id: str
    state: Literal["running", "ended"]
    started_at: datetime
    ends_at: datetime
    ended_at: datetime | None = None
    players: tuple[PlayerRegistration, ...]


def parse_duration(value: str) -> timedelta:
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError("scenario duration must be a positive integer followed by s, m, h, or d")
    return timedelta(seconds=int(match.group("amount")) * _SECONDS[match.group("unit")])


def connect() -> psycopg.Connection:
    url = os.environ.get("RANGE_CORE_PG_DSN", os.environ.get("PURPLE_PG_DSN", DEFAULT_DSN))
    return psycopg.connect(url, autocommit=True, connect_timeout=3)


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,))
    try:
        conn.execute(SCHEMA)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,))


def truncate_all(conn: psycopg.Connection) -> None:
    conn.execute(
        "TRUNCATE exercise_blue_actions, exercise_hint_usages, "
        "exercise_objective_completions, exercise_players, exercises"
    )


class ExerciseStore:
    """Start and retrieve exercises behind one PostgreSQL-backed interface."""

    def __init__(self, conn: psycopg.Connection, *, clock: Clock | None = None) -> None:
        self._conn = conn
        self._clock = clock or SystemClock()

    def start(
        self,
        scenario: Scenario,
        players: tuple[PlayerRegistration, ...],
    ) -> Exercise:
        if not players:
            raise ValueError("an exercise requires at least one Red player")
        started_at = self._clock.now()
        exercise_id = "ex-" + uuid.uuid4().hex
        ends_at = started_at + parse_duration(scenario.duration)
        self._expire_due(started_at)
        try:
            with self._conn.transaction():
                self._conn.execute(
                    """
                    INSERT INTO exercises
                        (exercise_id, scenario_id, state, started_at, ends_at)
                    VALUES (%s, %s, 'running', %s, %s)
                    """,
                    (exercise_id, scenario.id, started_at, ends_at),
                )
                with self._conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO exercise_players (exercise_id, player_id, source_ip)
                        VALUES (%s, %s, %s)
                        """,
                        [(exercise_id, player.player_id, player.source_ip) for player in players],
                    )
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "exercises_one_running_idx":
                raise ExerciseAlreadyRunning("an exercise is already running") from exc
            raise
        current = self._by_id(exercise_id)
        assert current is not None
        return current

    def current(self) -> Exercise | None:
        self._expire_due(self._clock.now())
        row = self._conn.execute(
            """
            SELECT exercise_id, scenario_id, state, started_at, ends_at, ended_at
            FROM exercises
            WHERE state = 'running'
            """
        ).fetchone()
        return self._hydrate(row) if row is not None else None

    def get(self, exercise_id: str) -> Exercise | None:
        return self._by_id(exercise_id)

    def reset_current(self) -> str | None:
        """Delete the running exercise and all derived state in one transaction.

        Audit events intentionally live outside this aggregate and have no
        cascading foreign key, so reset cannot erase the evidence trail.
        """

        with self._conn.transaction():
            row = self._conn.execute(
                """
                DELETE FROM exercises
                WHERE state = 'running'
                RETURNING exercise_id
                """
            ).fetchone()
        return row[0] if row is not None else None

    def _expire_due(self, now: datetime) -> None:
        self._conn.execute(
            """
            UPDATE exercises
            SET state = 'ended', ended_at = ends_at
            WHERE state = 'running' AND ends_at <= %s
            """,
            (now,),
        )

    def _by_id(self, exercise_id: str) -> Exercise | None:
        row = self._conn.execute(
            """
            SELECT exercise_id, scenario_id, state, started_at, ends_at, ended_at
            FROM exercises
            WHERE exercise_id = %s
            """,
            (exercise_id,),
        ).fetchone()
        return self._hydrate(row) if row is not None else None

    def _hydrate(self, row: tuple) -> Exercise:
        players = self._conn.execute(
            """
            SELECT player_id, host(source_ip)
            FROM exercise_players
            WHERE exercise_id = %s
            ORDER BY player_id
            """,
            (row[0],),
        ).fetchall()
        return Exercise(
            exercise_id=row[0],
            scenario_id=row[1],
            state=row[2],
            started_at=row[3],
            ends_at=row[4],
            ended_at=row[5],
            players=tuple(
                PlayerRegistration(player_id=player_id, source_ip=source_ip)
                for player_id, source_ip in players
            ),
        )
