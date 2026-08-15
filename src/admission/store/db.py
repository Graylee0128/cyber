from __future__ import annotations

import os

import psycopg

DEFAULT_DSN = "postgresql://purple:purple@localhost:5432/purple"
CONNECT_TIMEOUT_S = 3

#: 座位狀態的完整列舉，順序即中控畫面由左至右的敘事順序（尚未領 → 已在用 → 已結束）。
#: 與下方 `SCHEMA` 裡 seat.state 的 CHECK 約束是同一份清單，
#: `tests/admission/test_admission_api.py` 有測試在管兩邊不准漂開。
SEAT_STATES: tuple[str, ...] = (
    "free", "requested", "ready", "claimed", "failed", "released",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seat (
    seat_id     text        PRIMARY KEY,
    exercise_id text        NOT NULL,
    team        text        NOT NULL CHECK (team IN ('red', 'blue')),
    kind        text        NOT NULL CHECK (kind IN ('shell', 'console')),
    endpoints   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    state       text        NOT NULL DEFAULT 'free'
                 CHECK (state IN ('free','requested','ready','claimed','failed','released')),
    player_id   text,
    claimed_at  timestamptz,
    requested_at timestamptz,
    retry_count integer NOT NULL DEFAULT 0,
    published_at timestamptz
);

ALTER TABLE seat ADD COLUMN IF NOT EXISTS requested_at timestamptz;
ALTER TABLE seat ADD COLUMN IF NOT EXISTS retry_count integer NOT NULL DEFAULT 0;
ALTER TABLE seat ADD COLUMN IF NOT EXISTS published_at timestamptz;

CREATE INDEX IF NOT EXISTS seat_pool_idx ON seat (exercise_id, team, state);

-- 池上限（決策17，spec §3.2.1）：instructor 開場前設，紅藍語意不同（§4.3）
--   red_cap  = 紅池最多可以動態長到幾個座位（INSERT 時的天花板）
--   blue_cap = 開場時要預先建幾段（bulk-build 的數量，不是動態上限）
--   locked_at = exercise start 寫入；非 NULL 代表藍池已鎖，之後不再開放自助領號（決策25）
CREATE TABLE IF NOT EXISTS exercise_pool_config (
    exercise_id text        PRIMARY KEY,
    red_cap     integer     NOT NULL,
    blue_cap    integer     NOT NULL,
    locked_at   timestamptz
);

CREATE TABLE IF NOT EXISTS admission_session (
    token_hash text PRIMARY KEY,
    seat_id text NOT NULL REFERENCES seat(seat_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    revoked_at timestamptz
);
ALTER TABLE admission_session ADD COLUMN IF NOT EXISTS expires_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session_per_seat
    ON admission_session(seat_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS admission_remote_link (
    link_id uuid NOT NULL UNIQUE,
    token_hash text PRIMARY KEY,
    exercise_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    used_at timestamptz,
    revoked_at timestamptz
);
ALTER TABLE admission_remote_link ADD COLUMN IF NOT EXISTS link_id uuid;
ALTER TABLE admission_remote_link ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE admission_remote_link ADD COLUMN IF NOT EXISTS revoked_at timestamptz;
CREATE UNIQUE INDEX IF NOT EXISTS admission_remote_link_id_idx
    ON admission_remote_link(link_id);

CREATE TABLE IF NOT EXISTS admission_audit (
    audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    seat_id text NOT NULL,
    action text NOT NULL CHECK (action IN ('rebind', 'release', 'revoke_remote_link'))
);
ALTER TABLE admission_audit DROP CONSTRAINT IF EXISTS admission_audit_action_check;
ALTER TABLE admission_audit ADD CONSTRAINT admission_audit_action_check
    CHECK (action IN ('rebind', 'release', 'revoke_remote_link'));

CREATE TABLE IF NOT EXISTS admission_alert (
    alert_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    seat_id text NOT NULL,
    reason text NOT NULL,
    UNIQUE (seat_id, reason)
);

-- #126 item 2：教官的瀏覽器 session（不是伺服器對伺服器的 Bearer token）。
-- 沿用同一份 instructor_tokens 當憑證來源，這裡只是多一種出示方式——
-- 表單登入換 cookie，供 nginx auth_request 用；不綁 seat_id，因為教官不是座位。
CREATE TABLE IF NOT EXISTS admission_instructor_session (
    token_hash text PRIMARY KEY,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
"""


def dsn() -> str:
    return os.environ.get("ADMISSION_PG_DSN") or os.environ.get("PURPLE_PG_DSN", DEFAULT_DSN)


def connect(url: str | None = None, connect_timeout: int = CONNECT_TIMEOUT_S) -> psycopg.Connection:
    return psycopg.connect(url or dsn(), autocommit=True, connect_timeout=connect_timeout)


#: ensure_schema 的 advisory lock key。跟 purple 的 0x50555250（"PURP"）錯開，
#: 避免兩個服務同時起來時互相搶鎖。
SCHEMA_LOCK_KEY = 0x41444D31  # "ADM1"


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,))
    try:
        conn.execute(SCHEMA)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,))


def truncate_all(conn: psycopg.Connection) -> None:
    """測試專用：TRUNCATE 但保留 schema，跑完馬上乾淨。"""
    conn.execute(
        "TRUNCATE admission_alert, admission_audit, admission_session, admission_instructor_session, "
        "admission_remote_link, seat, exercise_pool_config RESTART IDENTITY"
    )
