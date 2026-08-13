from __future__ import annotations

import os

import psycopg

DEFAULT_DSN = "postgresql://purple:purple@localhost:5432/purple"
CONNECT_TIMEOUT_S = 3

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
    claimed_at  timestamptz
);

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
"""


def dsn() -> str:
    return os.environ.get("ADMISSION_PG_DSN", DEFAULT_DSN)


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
    conn.execute("TRUNCATE seat, exercise_pool_config")