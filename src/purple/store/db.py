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
    -- Action↔Evidence 的唯一關聯鍵（#90 Phase 1）。NULL＝不對應任何註冊動作。
    -- 提到欄位而非只留在 jsonb 裡，是為了讓「依動作取證據」是索引查詢而不是全表掃描。
    action_id    text,
    -- SSE 續傳的游標（#36）。recorded_at 當不了游標：now() 是交易開始時間，
    -- 兩筆並行寫入可能以「時間較早的那筆後 commit」的順序變成可見，訂閱者游標
    -- 越過去之後那筆就再也補不回來。序號只需嚴格遞增，不需連續 ——
    -- ON CONFLICT DO NOTHING 吃掉的重送仍會消耗一個號碼，gap 是正常的。
    seq          bigserial,
    event        jsonb       NOT NULL,
    -- firing 與 resolved 共用 event_id，用 lifecycle 區分（spec §2.2）
    PRIMARY KEY (event_id, lifecycle)
);

-- 既有資料庫升級路徑：CREATE TABLE IF NOT EXISTS 不會替已存在的表補欄位。
ALTER TABLE core_events ADD COLUMN IF NOT EXISTS action_id text;
ALTER TABLE core_events ADD COLUMN IF NOT EXISTS seq bigserial;

-- A serial value by itself is monotonic in allocation order, not commit
-- order. With concurrent receiver requests, transaction B could allocate 2
-- and commit before transaction A (which already allocated 1); an SSE reader
-- would advance to 2 and permanently skip 1. Put the transaction-scoped lock
-- *before* nextval in the DEFAULT expression, so allocation and commit are
-- serialized for every writer, including direct SQL writers.
CREATE OR REPLACE FUNCTION next_core_event_stream_seq()
RETURNS bigint LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(1129270853); -- "CORE"-scoped fixed key
    RETURN nextval('core_events_seq_seq');
END;
$$;
ALTER TABLE core_events ALTER COLUMN seq SET DEFAULT next_core_event_stream_seq();

CREATE UNIQUE INDEX IF NOT EXISTS core_events_seq_idx ON core_events (seq);
CREATE INDEX IF NOT EXISTS core_events_recorded_at_idx ON core_events (recorded_at);
CREATE INDEX IF NOT EXISTS core_events_exercise_idx    ON core_events (exercise_id, observed_at);
CREATE INDEX IF NOT EXISTS core_events_action_idx      ON core_events (exercise_id, action_id)
    WHERE action_id IS NOT NULL;

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

-- 紅隊執行的 ground truth（#90 Phase 1）。這是 `not_executed` 與動作時間窗的
-- 唯一真相來源：沒有這張表，「沒執行」與「執行了但沒被偵測」分不開。
--
-- 外鍵指向 registered_actions：**沒註冊過的動作不得有執行紀錄**。分母只能由凍結
-- 清單推導，允許孤兒執行紀錄等於開了一條從現場事件反推分母的後門。
CREATE TABLE IF NOT EXISTS action_executions (
    exercise_id text        NOT NULL,
    action_id   text        NOT NULL,
    executed_at timestamptz NOT NULL,
    window_end  timestamptz NOT NULL,
    marker      text        NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (exercise_id, action_id),
    FOREIGN KEY (exercise_id, action_id)
        REFERENCES registered_actions (exercise_id, action_id) ON DELETE CASCADE,
    CONSTRAINT action_executions_window_ordered CHECK (window_end >= executed_at),
    -- marker 是「這行 raw 遙測屬於哪個動作」的唯一判準。沒有 marker 就只能靠
    -- 時間窗猜，而兩個時間重疊的動作會互相偷對方的遙測 —— 於是可見性缺口與
    -- 偵測缺口的分界線變成擲骰子。沒 marker 的動作不得進入計分路徑。
    CONSTRAINT action_executions_marker_present CHECK (marker <> '')
);

-- Latency 量測的持久化（#90 Phase 4）。存的是**已算好的 p50/p95 摘要**，不是每筆
-- 原始樣本 —— 報告要的是分佈端點，重烤原始樣本沒有意義且會讓歷史數字浮動。
--
-- 主鍵含 mode：`exercise` 與 `automatic` 兩個模式各存一列，永不合併成單一分佈
-- （見 latency.py 的 MTTR_MODE_NOTE，人在迴圈的 MTTR 與自動模式混比會誤讀成退步）。
-- computed_at 存下來，讓「這份摘要是何時算的」可查 —— 與 action_registries.frozen_at
-- 同精神：一個數字要能說出自己的時效。
CREATE TABLE IF NOT EXISTS latency_summaries (
    exercise_id        text        NOT NULL,
    mode               text        NOT NULL,
    sample_count       int         NOT NULL,
    mttd_p50_ms        int,
    mttd_p95_ms        int,
    mttr_p50_ms        int,
    mttr_p95_ms        int,
    containment_p50_ms int,
    containment_p95_ms int,
    computed_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (exercise_id, mode),
    CONSTRAINT latency_summaries_sample_count_nonneg CHECK (sample_count >= 0)
);

CREATE OR REPLACE FUNCTION reject_frozen_action_registry_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE source_frozen timestamptz;
DECLARE destination_frozen timestamptz;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        SELECT frozen_at INTO source_frozen FROM action_registries
        WHERE exercise_id = OLD.exercise_id FOR SHARE;
    END IF;
    IF source_frozen IS NOT NULL THEN
        RAISE EXCEPTION 'action registry % is frozen', OLD.exercise_id
            USING ERRCODE = '55000';
    END IF;

    IF TG_OP <> 'DELETE' AND
       (TG_OP = 'INSERT' OR NEW.exercise_id IS DISTINCT FROM OLD.exercise_id) THEN
        SELECT frozen_at INTO destination_frozen FROM action_registries
        WHERE exercise_id = NEW.exercise_id FOR SHARE;
    END IF;
    IF destination_frozen IS NOT NULL THEN
        RAISE EXCEPTION 'action registry % is frozen', NEW.exercise_id
            USING ERRCODE = '55000';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE OR REPLACE FUNCTION reject_frozen_action_registry_header_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.frozen_at IS NOT NULL THEN
        RAISE EXCEPTION 'action registry % is frozen', OLD.exercise_id
            USING ERRCODE = '55000';
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.exercise_id IS DISTINCT FROM OLD.exercise_id OR
        NEW.scenario_id IS DISTINCT FROM OLD.scenario_id OR
        NEW.frozen_at IS NULL
    ) THEN
        RAISE EXCEPTION 'action registry header is immutable; only freeze is allowed'
            USING ERRCODE = '55000';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

DROP TRIGGER IF EXISTS registered_actions_reject_frozen ON registered_actions;
CREATE TRIGGER registered_actions_reject_frozen
BEFORE INSERT OR UPDATE OR DELETE ON registered_actions
FOR EACH ROW EXECUTE FUNCTION reject_frozen_action_registry_mutation();

DROP TRIGGER IF EXISTS action_registries_reject_frozen ON action_registries;
CREATE TRIGGER action_registries_reject_frozen
BEFORE UPDATE OR DELETE ON action_registries
FOR EACH ROW EXECUTE FUNCTION reject_frozen_action_registry_header_mutation();
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
        "TRUNCATE latency_summaries, action_executions, registered_actions, "
        "action_registries, core_events, alert_records, alert_fingerprints"
    )
