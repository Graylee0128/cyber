"""`alert_fingerprints` 從全域鍵升級到 (exercise_id, fingerprint) 的路徑。

這條路徑在真實資料庫上只會走一次，而且是在演練當天的既有資料上走 —— 錯了不會
噴錯，只會讓 firing 配不到 resolved。所以它需要測試，不能靠「跑過一次沒事」。

每個測試先把表打回舊形狀（`fingerprint text PRIMARY KEY`），再呼叫 `ensure_schema`，
驗證遷移後的結果。`ensure_schema` 執行完表就回到正確形狀，不影響其他測試。
"""

from __future__ import annotations

import json

import pytest

from purple.store.db import ensure_schema

LEGACY_TABLE = """
DROP TABLE IF EXISTS alert_fingerprints;
CREATE TABLE alert_fingerprints (
    fingerprint text        PRIMARY KEY,
    event_id    text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

LEGACY_TABLE_WITH_ODD_PK_NAME = """
DROP TABLE IF EXISTS alert_fingerprints;
CREATE TABLE alert_fingerprints (
    fingerprint text        NOT NULL,
    event_id    text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fingerprints_legacy_key PRIMARY KEY (fingerprint)
);
"""


def primary_key_columns(conn) -> list[str]:
    row = conn.execute(
        """
        SELECT array_agg(att.attname ORDER BY key_position.ordinality)
        FROM pg_constraint con
        CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS key_position(attnum, ordinality)
        JOIN pg_attribute att
          ON att.attrelid = con.conrelid AND att.attnum = key_position.attnum
        WHERE con.conrelid = 'alert_fingerprints'::regclass AND con.contype = 'p'
        """
    ).fetchone()
    return list(row[0]) if row and row[0] else []


def seed_core_event(conn, event_id: str, exercise_id: str) -> None:
    conn.execute(
        """
        INSERT INTO core_events
            (event_id, lifecycle, event_type, exercise_id, scenario_id, observed_at, action_id, event)
        VALUES (%s, 'firing', 'attack.detected', %s, 'sqli-01', now(), NULL, %s)
        """,
        (event_id, exercise_id, json.dumps({"event_id": event_id})),
    )


@pytest.fixture
def legacy_fingerprints(pg_connection):
    """把 alert_fingerprints 打回舊形狀，回傳連線。"""
    pg_connection.execute(LEGACY_TABLE)
    return pg_connection


def test_legacy_rows_are_backfilled_from_core_events(legacy_fingerprints) -> None:
    """舊資料列的場次抄自 core_events，不是丟掉。

    丟掉會讓升級當下正在進行的 firing 永遠等不到 resolved（契約 §2.2），
    而 event_id 明明就指得到 core_events，場次是抄得到的。
    """
    conn = legacy_fingerprints
    seed_core_event(conn, "evt-live", "ex-in-progress")
    conn.execute(
        "INSERT INTO alert_fingerprints (fingerprint, event_id) VALUES (%s, %s)",
        ("fp-live", "evt-live"),
    )

    ensure_schema(conn)

    row = conn.execute(
        "SELECT exercise_id, event_id FROM alert_fingerprints WHERE fingerprint = 'fp-live'"
    ).fetchone()
    assert row == ("ex-in-progress", "evt-live")
    assert primary_key_columns(conn) == ["exercise_id", "fingerprint"]


def test_rows_whose_event_no_longer_exists_are_dropped(legacy_fingerprints) -> None:
    """core_events 裡已經沒有對應事件的列配不到任何東西，刪掉。"""
    conn = legacy_fingerprints
    conn.execute(
        "INSERT INTO alert_fingerprints (fingerprint, event_id) VALUES (%s, %s)",
        ("fp-orphan", "evt-long-gone"),
    )

    ensure_schema(conn)

    assert conn.execute("SELECT count(*) FROM alert_fingerprints").fetchone()[0] == 0


def test_primary_key_is_found_by_lookup_not_by_its_default_name(pg_connection) -> None:
    """主鍵叫什麼名字是查出來的。

    `alert_fingerprints_pkey` 只是 PG 的預設命名；手動建過或從備份還原過的
    資料庫可能叫別的名字。硬寫名字的 `DROP CONSTRAINT IF EXISTS` 會靜默跳過，
    舊的單欄主鍵留在原地，第二場演練的同一個 fingerprint 就會撞上它。
    """
    conn = pg_connection
    conn.execute(LEGACY_TABLE_WITH_ODD_PK_NAME)
    assert primary_key_columns(conn) == ["fingerprint"]

    ensure_schema(conn)

    assert primary_key_columns(conn) == ["exercise_id", "fingerprint"]


def test_migration_is_idempotent_and_keeps_data(legacy_fingerprints) -> None:
    """`ensure_schema` 每次啟動都跑，所以第二次不得再動資料。"""
    conn = legacy_fingerprints
    seed_core_event(conn, "evt-keep", "ex-keep")
    conn.execute(
        "INSERT INTO alert_fingerprints (fingerprint, event_id) VALUES (%s, %s)",
        ("fp-keep", "evt-keep"),
    )

    ensure_schema(conn)
    ensure_schema(conn)

    rows = conn.execute(
        "SELECT exercise_id, fingerprint, event_id FROM alert_fingerprints"
    ).fetchall()
    assert rows == [("ex-keep", "fp-keep", "evt-keep")]
    assert primary_key_columns(conn) == ["exercise_id", "fingerprint"]


def test_two_exercises_can_hold_the_same_fingerprint_after_migration(legacy_fingerprints) -> None:
    """遷移的目的：同一個 fingerprint 在兩場演練裡各有各的 event_id。"""
    conn = legacy_fingerprints
    ensure_schema(conn)

    conn.execute(
        "INSERT INTO alert_fingerprints (exercise_id, fingerprint, event_id) "
        "VALUES ('ex-1', 'fp-same-rule', 'evt-1'), ('ex-2', 'fp-same-rule', 'evt-2')"
    )

    rows = conn.execute(
        "SELECT exercise_id, event_id FROM alert_fingerprints "
        "WHERE fingerprint = 'fp-same-rule' ORDER BY exercise_id"
    ).fetchall()
    assert rows == [("ex-1", "evt-1"), ("ex-2", "evt-2")]
