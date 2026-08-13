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

CREATE TABLE IF NOT EXISTS exercise_preparations (
    exercise_id text        PRIMARY KEY,
    scenario_id text        NOT NULL,
    state       text        NOT NULL CHECK (state IN ('prepared', 'started')),
    prepared_at timestamptz NOT NULL,
    started_at  timestamptz,
    CHECK ((state = 'prepared' AND started_at IS NULL)
        OR (state = 'started' AND started_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS exercise_preparations_one_prepared_idx
    ON exercise_preparations ((state))
    WHERE state = 'prepared';

CREATE TABLE IF NOT EXISTS admission_players (
    exercise_id  text        NOT NULL REFERENCES exercise_preparations(exercise_id),
    player_id    text        NOT NULL,
    team         text        NOT NULL CHECK (team IN ('red', 'blue')),
    source_ip    inet,
    active       boolean     NOT NULL DEFAULT true,
    registered_at timestamptz NOT NULL,
    revoked_at   timestamptz,
    PRIMARY KEY (exercise_id, player_id),
    CHECK ((team = 'red' AND source_ip IS NOT NULL)
        OR (team = 'blue' AND source_ip IS NULL)),
    CHECK ((active AND revoked_at IS NULL)
        OR (NOT active AND revoked_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS admission_players_active_source_idx
    ON admission_players (exercise_id, source_ip)
    WHERE active AND source_ip IS NOT NULL;

CREATE TABLE IF NOT EXISTS exercise_players (
    exercise_id text NOT NULL REFERENCES exercises(exercise_id) ON DELETE CASCADE,
    player_id   text NOT NULL,
    source_ip   inet NOT NULL,
    active      boolean NOT NULL DEFAULT true,
    revoked_at  timestamptz,
    PRIMARY KEY (exercise_id, player_id),
    CONSTRAINT exercise_players_active_revocation_check CHECK (
        (active AND revoked_at IS NULL)
        OR (NOT active AND revoked_at IS NOT NULL)
    )
);

ALTER TABLE exercise_players ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE exercise_players ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
ALTER TABLE exercise_players DROP CONSTRAINT IF EXISTS exercise_players_active_revocation_check;
ALTER TABLE exercise_players ADD CONSTRAINT exercise_players_active_revocation_check CHECK (
    (active AND revoked_at IS NULL)
    OR (NOT active AND revoked_at IS NOT NULL)
);
ALTER TABLE exercise_players DROP CONSTRAINT IF EXISTS exercise_players_exercise_id_source_ip_key;
CREATE UNIQUE INDEX IF NOT EXISTS exercise_players_active_source_idx
    ON exercise_players (exercise_id, source_ip)
    WHERE active;

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
LIFECYCLE_LOCK_KEY = 0x575338  # "WS8"
DEFAULT_DSN = "postgresql://purple:purple@localhost:5432/purple"
_DURATION = re.compile(r"^(?P<amount>[1-9][0-9]*)(?P<unit>[smhd])$")
_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
RED_SEAT_ADDRESSES = frozenset(
    ipaddress.ip_address(f"10.167.30.{last_octet}") for last_octet in range(11, 255)
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ExerciseAlreadyRunning(RuntimeError):
    """PostgreSQL rejected a second row covered by the running-only index."""


class ExerciseNotPrepared(LookupError):
    """The Admission exercise identifier is unknown or no longer prepared."""


class PlayerRegistrationConflict(RuntimeError):
    """An idempotency key was reused with different player attributes."""


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
        if address not in RED_SEAT_ADDRESSES:
            raise ValueError("source_ip must be a Z-RED seat address 10.167.30.11 through .254")
        return str(address)


class PrepareExercise(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exercise_id: str
    scenario_id: str
    state: Literal["prepared", "started"]
    prepared_at: datetime
    started_at: datetime | None = None


class AdmissionPlayer(BaseModel):
    """Identity published by Admission only when its seat reaches ready."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exercise_id: str
    player_id: str
    team: Literal["red", "blue"]
    source_ip: str | None = None
    state: Literal["active", "revoked"]
    registered_at: datetime
    revoked_at: datetime | None = None


class AdmissionPlayerRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    team: Literal["red", "blue"]
    source_ip: str | None = None

    @field_validator("source_ip")
    @classmethod
    def source_is_a_kali_address_when_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return PlayerRegistration(player_id="validation", source_ip=value).source_ip

    def validated(self) -> "AdmissionPlayerRegistration":
        if self.team == "red" and self.source_ip is None:
            raise ValueError("red players require source_ip")
        if self.team == "blue" and self.source_ip is not None:
            raise ValueError("blue players do not have a red source_ip")
        return self


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
        "exercise_objective_completions, exercise_players, exercises, "
        "admission_players, exercise_preparations"
    )


class ExerciseStore:
    """Start and retrieve exercises behind one PostgreSQL-backed interface."""

    def __init__(self, conn: psycopg.Connection, *, clock: Clock | None = None) -> None:
        self._conn = conn
        self._clock = clock or SystemClock()

    def now(self) -> datetime:
        """The lifecycle clock used for this store.

        Streaming owns a separate DB connection, but must use the same clock
        as lifecycle writes (including deterministic test clocks) when it
        decides whether ``ends_at`` has passed.
        """
        return self._clock.now()

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
                self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (LIFECYCLE_LOCK_KEY,))
                if self._conn.execute(
                    "SELECT 1 FROM exercise_preparations WHERE state = 'prepared'"
                ).fetchone():
                    raise ExerciseAlreadyRunning("an exercise is already prepared")
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

    def prepare(self, scenario: Scenario) -> PrepareExercise:
        """Reserve an exercise identity without making it current/running."""
        now = self._clock.now()
        self._expire_due(now)
        exercise_id = "ex-" + uuid.uuid4().hex
        try:
            with self._conn.transaction():
                self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (LIFECYCLE_LOCK_KEY,))
                if self._conn.execute(
                    "SELECT 1 FROM exercises WHERE state = 'running'"
                ).fetchone():
                    raise ExerciseAlreadyRunning("an exercise is already running")
                self._conn.execute(
                    """
                    INSERT INTO exercise_preparations
                        (exercise_id, scenario_id, state, prepared_at)
                    VALUES (%s, %s, 'prepared', %s)
                    """,
                    (exercise_id, scenario.id, now),
                )
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "exercise_preparations_one_prepared_idx":
                raise ExerciseAlreadyRunning("an exercise is already prepared") from exc
            raise
        prepared = self.preparation(exercise_id)
        assert prepared is not None
        return prepared

    def preparation(self, exercise_id: str) -> PrepareExercise | None:
        row = self._conn.execute(
            """
            SELECT exercise_id, scenario_id, state, prepared_at, started_at
            FROM exercise_preparations WHERE exercise_id = %s
            """,
            (exercise_id,),
        ).fetchone()
        return PrepareExercise(
            exercise_id=row[0], scenario_id=row[1], state=row[2],
            prepared_at=row[3], started_at=row[4]
        ) if row is not None else None

    def register_player(
        self,
        exercise_id: str,
        player_id: str,
        registration: AdmissionPlayerRegistration,
    ) -> AdmissionPlayer:
        registration = registration.validated()
        now = self._clock.now()
        with self._conn.transaction():
            preparation = self._conn.execute(
                "SELECT state FROM exercise_preparations WHERE exercise_id = %s FOR UPDATE",
                (exercise_id,),
            ).fetchone()
            if preparation is None:
                raise ExerciseNotPrepared("exercise is not prepared")
            if preparation[0] == "started" and self._conn.execute(
                "SELECT state FROM exercises WHERE exercise_id = %s",
                (exercise_id,),
            ).fetchone() != ("running",):
                raise ExerciseNotPrepared("exercise is no longer running")
            self._conn.execute(
                """
                INSERT INTO admission_players
                    (exercise_id, player_id, team, source_ip, registered_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (exercise_id, player_id) DO NOTHING
                """,
                (exercise_id, player_id, registration.team, registration.source_ip, now),
            )
            player = self.player(exercise_id, player_id, include_revoked=True)
            assert player is not None
            if (
                player.state != "active"
                or player.team != registration.team
                or player.source_ip != registration.source_ip
            ):
                raise PlayerRegistrationConflict(
                    "player_id is already registered with different attributes or revoked"
                )
            if preparation[0] == "started" and registration.team == "red":
                self._conn.execute(
                    """
                    INSERT INTO exercise_players (exercise_id, player_id, source_ip)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (exercise_id, player_id) DO NOTHING
                    """,
                    (exercise_id, player_id, registration.source_ip),
                )
        return player

    def player(
        self, exercise_id: str, player_id: str, *, include_revoked: bool = False
    ) -> AdmissionPlayer | None:
        row = self._conn.execute(
            """
            SELECT exercise_id, player_id, team, host(source_ip), active,
                   registered_at, revoked_at
            FROM admission_players
            WHERE exercise_id = %s AND player_id = %s
              AND (%s OR active)
            """,
            (exercise_id, player_id, include_revoked),
        ).fetchone()
        if row is None:
            return None
        return AdmissionPlayer(
            exercise_id=row[0], player_id=row[1], team=row[2], source_ip=row[3],
            state="active" if row[4] else "revoked", registered_at=row[5], revoked_at=row[6]
        )

    def revoke_player(self, exercise_id: str, player_id: str) -> bool:
        """Revoke future attribution while retaining player and completion rows."""
        now = self._clock.now()
        with self._conn.transaction():
            row = self._conn.execute(
                """
                UPDATE admission_players
                SET active = false, revoked_at = %s
                WHERE exercise_id = %s AND player_id = %s AND active
                RETURNING player_id
                """,
                (now, exercise_id, player_id),
            ).fetchone()
            exists = row is not None or self._conn.execute(
                "SELECT 1 FROM admission_players WHERE exercise_id = %s AND player_id = %s",
                (exercise_id, player_id),
            ).fetchone() is not None
            if exists:
                self._conn.execute(
                    """
                    UPDATE exercise_players
                    SET active = false, revoked_at = COALESCE(revoked_at, %s)
                    WHERE exercise_id = %s AND player_id = %s
                    """,
                    (now, exercise_id, player_id),
                )
        return exists

    def start_prepared(self, exercise_id: str, scenario: Scenario) -> Exercise:
        started_at = self._clock.now()
        ends_at = started_at + parse_duration(scenario.duration)
        self._expire_due(started_at)
        try:
            with self._conn.transaction():
                self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (LIFECYCLE_LOCK_KEY,))
                prepared = self._conn.execute(
                    """
                    SELECT scenario_id, state FROM exercise_preparations
                    WHERE exercise_id = %s FOR UPDATE
                    """,
                    (exercise_id,),
                ).fetchone()
                if prepared is None or prepared[1] != "prepared" or prepared[0] != scenario.id:
                    raise ExerciseNotPrepared("exercise is not prepared for this scenario")
                self._conn.execute(
                    """
                    INSERT INTO exercises
                        (exercise_id, scenario_id, state, started_at, ends_at)
                    VALUES (%s, %s, 'running', %s, %s)
                    """,
                    (exercise_id, scenario.id, started_at, ends_at),
                )
                self._conn.execute(
                    """
                    INSERT INTO exercise_players (exercise_id, player_id, source_ip)
                    SELECT exercise_id, player_id, source_ip
                    FROM admission_players
                    WHERE exercise_id = %s AND team = 'red' AND active
                    """,
                    (exercise_id,),
                )
                self._conn.execute(
                    """
                    UPDATE exercise_preparations
                    SET state = 'started', started_at = %s
                    WHERE exercise_id = %s
                    """,
                    (started_at, exercise_id),
                )
        except psycopg.errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "exercises_one_running_idx":
                raise ExerciseAlreadyRunning("an exercise is already running") from exc
            raise
        started = self._by_id(exercise_id)
        assert started is not None
        return started

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
            WHERE exercise_id = %s AND active
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
