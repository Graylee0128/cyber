"""Core Event 的儲存與讀取。

載具靠 `since()` 捕捉「我發動攻擊之後」出現的事件 —— 用 recorded_at 而非
observed_at，因為 observed_at 是遙測來源給的，可能落在攻擊之前。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

INSERT = """
INSERT INTO core_events
    (event_id, lifecycle, event_type, exercise_id, scenario_id, observed_at, event)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id, lifecycle) DO NOTHING
"""


@dataclass
class CoreEventStore:
    conn: psycopg.Connection

    def append(self, event: dict[str, Any]) -> None:
        """寫入一筆事件。同 event_id ＋ lifecycle 重送不會產生第二筆 ——
        Grafana webhook 會重試，重試不該讓 coverage 分子灌水。"""
        self.conn.execute(
            INSERT,
            (
                event["event_id"],
                event["lifecycle"],
                event["event_type"],
                event["exercise_id"],
                event["scenario_id"],
                event["observed_at"],
                Jsonb(event),
            ),
        )

    def since(self, moment: datetime) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT event FROM core_events WHERE recorded_at > %s ORDER BY recorded_at",
            (moment,),
        ).fetchall()
        return [row[0] for row in rows]

    def by_id(self, event_id: str) -> list[dict[str, Any]]:
        """同一個 event_id 可能有 firing 與 resolved 兩筆，依 lifecycle 排序回傳。"""
        rows = self.conn.execute(
            "SELECT event FROM core_events WHERE event_id = %s ORDER BY recorded_at",
            (event_id,),
        ).fetchall()
        return [row[0] for row in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM core_events").fetchone()[0]

    def now(self) -> datetime:
        """資料庫的時間，不是本機的。載具用它取「攻擊開始」的時刻 ——
        用本機時鐘會把票 01 想消滅的偏差重新引進來。"""
        return self.conn.execute("SELECT now()").fetchone()[0]
