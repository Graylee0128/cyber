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

import logging
import os

import psycopg

log = logging.getLogger("purple.store.db")

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
--
-- 鍵**必須**含 exercise_id：Grafana 的 fingerprint 只由 rule 的 label 集合決定，
-- 同一條規則在下一場演練會再次產生一模一樣的 fingerprint。全域鍵會讓第二場的
-- firing 撈回第一場鑄造的 event_id —— 新事件被靜默掛到已結束場次的 id 上，
-- 不會噴任何錯。
CREATE TABLE IF NOT EXISTS alert_fingerprints (
    exercise_id text        NOT NULL,
    fingerprint text        NOT NULL,
    event_id    text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (exercise_id, fingerprint)
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


#: 既有資料庫的升級路徑，與 `SCHEMA` 分開。
#:
#: `SCHEMA` 回答「現在該長什麼樣」，`MIGRATIONS` 回答「舊的怎麼變成現在這樣」——
#: 兩件事混在同一段 SQL 裡，表格宣告會被遷移邏輯切斷，而且讀的人分不出哪幾行
#: 是常態、哪幾行是過渡。兩者都必須可重複執行（每次 `ensure_schema` 都會跑）。
MIGRATIONS = """
DO $$
DECLARE
    pk_name    text;
    pk_columns text[];
    stale_idx  text;
    stale_con  text;
    backfilled bigint;
    orphaned   bigint;
BEGIN
    -- alert_fingerprints 的鍵從 (fingerprint) 換成 (exercise_id, fingerprint)。
    -- CREATE TABLE IF NOT EXISTS 不會替已存在的表補欄位或換主鍵，所以要手動補。
    ALTER TABLE alert_fingerprints ADD COLUMN IF NOT EXISTS exercise_id text;

    -- 舊資料列的場次**不是無從得知**，只是還沒抄過來：alert_fingerprints.event_id
    -- 指向 core_events，而 core_events.exercise_id 是 NOT NULL。所以回填，不丟棄 ——
    -- 丟棄會讓升級當下正在進行的 firing 永遠等不到它的 resolved 配對（契約 §2.2），
    -- 而且無聲無息。ADR 0003 對 revocation 的處理也是保留紀錄而非刪除。
    UPDATE alert_fingerprints fp
       SET exercise_id = ce.exercise_id
      FROM core_events ce
     WHERE fp.exercise_id IS NULL
       AND ce.event_id = fp.event_id;
    GET DIAGNOSTICS backfilled = ROW_COUNT;

    -- 只有回填不到的才刪：event_id 在 core_events 裡沒有對應（事件已被清空）。
    -- 這種列配不到任何東西了，但刪掉仍必須留下痕跡，不得靜默。
    DELETE FROM alert_fingerprints WHERE exercise_id IS NULL;
    GET DIAGNOSTICS orphaned = ROW_COUNT;

    IF backfilled > 0 OR orphaned > 0 THEN
        RAISE NOTICE
            'alert_fingerprints 升級：回填 % 列；丟棄 % 列（event_id 在 core_events 中已無對應）',
            backfilled, orphaned;
    END IF;

    ALTER TABLE alert_fingerprints ALTER COLUMN exercise_id SET NOT NULL;

    -- 主鍵名稱用查的，不用猜的：`alert_fingerprints_pkey` 只是 PG 的預設命名，
    -- 手動建過或還原過的資料庫可能叫別的名字，硬寫的 DROP CONSTRAINT 會靜默跳過。
    -- attname 是 `name` 型別，array_agg 出來是 name[]。跟 text[] 沒有 `=` 運算子，
    -- 所以一律先轉 text 再比 —— 不轉會在執行期噴 operator does not exist。
    SELECT con.conname, array_agg(att.attname::text ORDER BY key_position.ordinality)
      INTO pk_name, pk_columns
      FROM pg_constraint con
      CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS key_position(attnum, ordinality)
      JOIN pg_attribute att
        ON att.attrelid = con.conrelid AND att.attnum = key_position.attnum
     WHERE con.conrelid = 'alert_fingerprints'::regclass
       AND con.contype = 'p'
     GROUP BY con.conname;

    IF pk_columns IS DISTINCT FROM ARRAY['exercise_id', 'fingerprint'] THEN
        IF pk_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE alert_fingerprints DROP CONSTRAINT %I', pk_name);
        END IF;

        -- 只建在 fingerprint 單欄上的舊唯一索引也要拿掉。換了主鍵卻留著它，
        -- 第二場演練的同一個 fingerprint 會撞上這個索引 —— 正是這次要修掉的
        -- 跨場次衝突，只是換成從索引層爆出來。
        FOR stale_idx, stale_con IN
            SELECT cls.relname::text, con.conname::text
              FROM pg_index idx
              JOIN pg_class cls ON cls.oid = idx.indexrelid
              LEFT JOIN pg_constraint con ON con.conindid = idx.indexrelid
             WHERE idx.indrelid = 'alert_fingerprints'::regclass
               AND idx.indisunique
               AND (SELECT array_agg(att.attname::text ORDER BY att.attnum)
                      FROM pg_attribute att
                     WHERE att.attrelid = idx.indrelid
                       AND att.attnum = ANY (idx.indkey)) = ARRAY['fingerprint']
        LOOP
            -- 由 constraint 撐著的索引不能直接 DROP INDEX（PG 會擋），要拆 constraint。
            IF stale_con IS NOT NULL THEN
                EXECUTE format('ALTER TABLE alert_fingerprints DROP CONSTRAINT %I', stale_con);
            ELSE
                EXECUTE format('DROP INDEX %I', stale_idx);
            END IF;
            RAISE NOTICE 'alert_fingerprints 升級：移除只涵蓋 fingerprint 的舊唯一索引 %', stale_idx;
        END LOOP;

        ALTER TABLE alert_fingerprints
            ADD CONSTRAINT alert_fingerprints_pkey PRIMARY KEY (exercise_id, fingerprint);
    END IF;
END
$$;
"""


def dsn() -> str:
    return os.environ.get("PURPLE_PG_DSN", DEFAULT_DSN)


def connect(url: str | None = None, connect_timeout: int = CONNECT_TIMEOUT_S) -> psycopg.Connection:
    return psycopg.connect(url or dsn(), autocommit=True, connect_timeout=connect_timeout)


#: ensure_schema 的 advisory lock key。任意固定值，同一 DB 上所有建立者共用。
SCHEMA_LOCK_KEY = 0x50555250  # "PURP"


def _log_pg_notice(diagnostic: psycopg.errors.Diagnostic) -> None:
    log.info("PostgreSQL %s：%s", diagnostic.severity, diagnostic.message_primary)


def ensure_schema(conn: psycopg.Connection) -> None:
    # `CREATE TABLE IF NOT EXISTS` 在並發下**不是原子的**：兩個建立者會同時通過
    # 「不存在」檢查、同時建立，撞上 pg_type 的唯一鍵（UniqueViolation）。
    # receiver / evaluation-engine / 測試會同時建 schema，用 advisory lock 序列化。
    conn.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK_KEY,))
    # PG 的 NOTICE 預設既不進伺服器 log（log_min_messages=warning）也不進應用程式
    # 的 log —— psycopg 收到就丟掉。遷移動到資料（回填／丟棄）時必須留下痕跡，
    # 所以在這裡把 NOTICE 接到 Python logging 上。
    conn.add_notice_handler(_log_pg_notice)
    try:
        conn.execute(SCHEMA)
        conn.execute(MIGRATIONS)
    finally:
        conn.remove_notice_handler(_log_pg_notice)
        conn.execute("SELECT pg_advisory_unlock(%s)", (SCHEMA_LOCK_KEY,))


def truncate_all(conn: psycopg.Connection) -> None:
    """測試之間清空。TRUNCATE 而非 DROP —— 保留 schema，快得多。"""
    conn.execute(
        "TRUNCATE latency_summaries, action_executions, registered_actions, "
        "action_registries, core_events, alert_records, alert_fingerprints"
    )
