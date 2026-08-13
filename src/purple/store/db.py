"""PostgreSQL 連線與 schema。

為什麼是 PG 而不是 SQLite（2026-08-08 決定）：

1. `timestamptz` —— 整個平台的核心是時序關聯。票 01 花了整段功夫防「無時區時間戳」，
   SQLite 把時間存成字串或數字，等於把剛擋掉的問題請回來。
2. receiver 與 Evaluation Engine 是兩個行程（spec §4）。SQLite 的單寫入者模型
   在這裡直接是錯的架構。
3. `jsonb` —— Alert Record 的 `labels`／`fired_values` 形狀不固定，
   SQLite 存得下但查不動。
"""

from __future__ import annotations

import os

import psycopg

#: 本機與 CI 都用這個。CI 由 service container 提供，本機由 docker compose。
DEFAULT_DSN = "postgresql://purple:purple@localhost:5432/purple"

#: 連線逾時。沒有它，連到一個「靜默丟包」的 port（防火牆 drop 而非 refuse）
#: 會卡到 OS 的 TCP timeout（Windows 上 ~21s／位址），讓「PG 沒起來」的
#: 失敗訊息拖上幾分鐘才出現。快速失敗才能快速修。
CONNECT_TIMEOUT_S = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS core_events (
    event_id     text        NOT NULL,
    lifecycle    text        NOT NULL,
    event_type   text        NOT NULL,
    exercise_id  text        NOT NULL,
    scenario_id  text        NOT NULL,
    observed_at  timestamptz NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    event        jsonb       NOT NULL,
    -- firing 與 resolved 共用 event_id，用 lifecycle 區分（spec §2.2）
    PRIMARY KEY (event_id, lifecycle)
);

CREATE INDEX IF NOT EXISTS core_events_recorded_at_idx ON core_events (recorded_at);
CREATE INDEX IF NOT EXISTS core_events_exercise_idx    ON core_events (exercise_id, observed_at);

CREATE TABLE IF NOT EXISTS alert_records (
    event_id     text        PRIMARY KEY,
    grafana_rule text        NOT NULL,
    query        text        NOT NULL,
    threshold    text,
    fired_values jsonb       NOT NULL DEFAULT '[]'::jsonb,
    labels       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    backend      text        NOT NULL,
    recorded_at  timestamptz NOT NULL DEFAULT now()
);

-- 一個 Grafana 告警的 firing 與 resolved 帶同一個 fingerprint。把 fingerprint
-- 對映到單一 event_id，resolved 才能和 firing 共用同一個 event_id（spec §2.2、票 05）。
CREATE TABLE IF NOT EXISTS alert_fingerprints (
    fingerprint text        PRIMARY KEY,
    event_id    text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action_registries (
    exercise_id text        PRIMARY KEY,
    scenario_id text        NOT NULL,
    frozen_at   timestamptz
);

CREATE TABLE IF NOT EXISTS registered_actions (
    exercise_id text NOT NULL REFERENCES action_registries(exercise_id) ON DELETE CASCADE,
    action_id   text NOT NULL,
    technique   text NOT NULL,
    description text NOT NULL,
    PRIMARY KEY (exercise_id, action_id)
);

CREATE OR REPLACE FUNCTION reject_frozen_action_registry_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE registry_id text;
BEGIN
    registry_id := COALESCE(NEW.exercise_id, OLD.exercise_id);
    IF EXISTS (
        SELECT 1 FROM action_registries
        WHERE exercise_id = registry_id AND frozen_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'action registry % is frozen', registry_id
            USING ERRCODE = '55000';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS registered_actions_reject_frozen ON registered_actions;
CREATE TRIGGER registered_actions_reject_frozen
BEFORE INSERT OR UPDATE OR DELETE ON registered_actions
FOR EACH ROW EXECUTE FUNCTION reject_frozen_action_registry_mutation();
"""


def dsn() -> str:
    return os.environ.get("PURPLE_PG_DSN", DEFAULT_DSN)


def connect(url: str | None = None, connect_timeout: int = CONNECT_TIMEOUT_S) -> psycopg.Connection:
    return psycopg.connect(url or dsn(), autocommit=True, connect_timeout=connect_timeout)


#: ensure_schema 的 advisory lock key。任意固定值，同一 DB 上所有建立者共用。
SCHEMA_LOCK_KEY = 0x50555250  # "PURP"


def ensure_schema(conn: psycopg.Connection) -> None:
    # `CREATE TABLE IF NOT EXISTS` 在並發下**不是原子的**：兩個建立者會同時通過
    # 「不存在」檢查、同時建立，撞上 pg_type 的唯一鍵（UniqueViolation）。
    # receiver / evaluation-engine / 測試會同時建 schema，用 advisory lock 序列化。
    conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,))
    try:
        conn.execute(SCHEMA)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,))


def truncate_all(conn: psycopg.Connection) -> None:
    """測試之間清空。TRUNCATE 而非 DROP —— 保留 schema，快得多。"""
    conn.execute(
        "TRUNCATE registered_actions, action_registries, "
        "core_events, alert_records, alert_fingerprints"
    )
