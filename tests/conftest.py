"""共用 fixture。

兩個 session 級前置條件，兩個都 **fail 不 skip**：

- `_require_postgres` —— PG 沒起來，整個 suite 不跑。2026-08-08 決定：
  測試一律需要 PG，不分層。理由是分層會讓「本機綠、CI 紅」的落差長期存在。
- `clock_in_sync` —— 票 01 的交付物，時序斷言的前提。

skip 是前置檢查退化成永遠綠的標準路徑：檢查壞了 → 自動跳過 → 沒人發現。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import psycopg
import pytest

from purple.clock.config import ConfigError, load_config
from purple.clock.runner import check
from purple.store.db import connect, dsn, ensure_schema, truncate_all
from range_core.exercises import (
    ensure_schema as ensure_exercise_schema,
    truncate_all as truncate_exercises,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOCK_CONFIG = REPO_ROOT / "config" / "clock-nodes.yaml"

#: 「本機與 CI 以同一指令啟動」—— 本機自動把 PG 帶起來，CI 由 service container 提供。
AUTO_COMPOSE = os.environ.get("PURPLE_AUTO_COMPOSE", "1") == "1"
STARTUP_TIMEOUT_S = 60


def _try_connect() -> psycopg.Connection | None:
    try:
        return connect()
    except psycopg.OperationalError:
        return None


def _compose_up() -> str | None:
    """回傳失敗原因，成功回 None。"""
    try:
        done = subprocess.run(
            ["docker", "compose", "up", "-d", "postgres"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=STARTUP_TIMEOUT_S,
        )
    except FileNotFoundError:
        return "docker CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return f"docker compose up timed out after {STARTUP_TIMEOUT_S}s"
    return None if done.returncode == 0 else done.stderr.strip()


@pytest.fixture(scope="session")
def pg() -> psycopg.Connection:
    conn = _try_connect()

    if conn is None and AUTO_COMPOSE:
        reason = _compose_up()
        if reason is None:
            deadline = time.monotonic() + STARTUP_TIMEOUT_S
            while conn is None and time.monotonic() < deadline:
                time.sleep(1)
                conn = _try_connect()

    if conn is None:
        pytest.fail(
            f"PostgreSQL 連不上（{dsn()}）。\n"
            f"整個測試套件需要 PG（2026-08-08 決定，見 map.md）。\n"
            f"  本機：docker compose up -d postgres\n"
            f"  其他：設 PURPLE_PG_DSN 指向既有的 PG\n"
            f"  停用自動啟動：PURPLE_AUTO_COMPOSE=0"
        )

    ensure_schema(conn)
    ensure_exercise_schema(conn)
    yield conn
    conn.close()


@pytest.fixture(scope="session", autouse=True)
def _require_postgres(pg):
    """所有測試都要求 PG。刻意不分層 —— 分層會讓「本機綠、CI 紅」長期存在。"""


@pytest.fixture
def pg_connection(pg) -> psycopg.Connection:
    """每個測試拿到乾淨的表。"""
    truncate_all(pg)
    truncate_exercises(pg)
    return pg


@pytest.fixture(scope="session")
def clock_in_sync():
    """時鐘同步的前置條件。不同步就讓測試 **失敗**，不是 skip。"""
    try:
        config = load_config(CLOCK_CONFIG)
    except ConfigError as exc:
        pytest.fail(f"clock config unusable: {exc}")

    report = check(config)
    if not report.ok:
        pytest.fail(f"時鐘未同步，後續時序斷言不可信：\n{report.summary()}")
    return report
