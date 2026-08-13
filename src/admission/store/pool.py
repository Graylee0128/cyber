"""exercise 池上限設定（決策17，spec §3.2.1）。

紅藍語意不同（§4.3）：
- red_cap  = 紅池最多可以動態長到幾個座位（seats.py 的 INSERT 路徑用它擋新增）
- blue_cap = 開場時要預先建幾段（seats.py 的 bulk_build_blue_seats 用它決定建幾張）
- locked_at = start 時寫入。非 NULL 之後，藍池不再接受自助領號（決策25）。
  紅池不受 locked_at 影響——start 後仍可持續動態領號，只受 red_cap 限制。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple

import psycopg


class PoolConfig(NamedTuple):
    exercise_id: str
    red_cap: int
    blue_cap: int
    locked_at: datetime | None


@dataclass
class PoolConfigStore:
    conn: psycopg.Connection

    def set_caps(self, exercise_id: str, red_cap: int, blue_cap: int) -> None:
        """instructor 開場前設定上限。可重複呼叫覆蓋（尚未 lock 之前）。"""
        self.conn.execute(
            """
            INSERT INTO exercise_pool_config (exercise_id, red_cap, blue_cap)
            VALUES (%s, %s, %s)
            ON CONFLICT (exercise_id) DO UPDATE
                SET red_cap = EXCLUDED.red_cap, blue_cap = EXCLUDED.blue_cap
                WHERE exercise_pool_config.locked_at IS NULL
            """,
            (exercise_id, red_cap, blue_cap),
        )

    def get(self, exercise_id: str) -> PoolConfig | None:
        row = self.conn.execute(
            "SELECT exercise_id, red_cap, blue_cap, locked_at FROM exercise_pool_config WHERE exercise_id = %s",
            (exercise_id,),
        ).fetchone()
        if row is None:
            return None
        return PoolConfig(*row)

    def lock(self, exercise_id: str) -> bool:
        """exercise start：鎖池。之後藍池不再接受自助領號（決策25）。"""
        row = self.conn.execute(
            """
            UPDATE exercise_pool_config SET locked_at = now()
            WHERE exercise_id = %s AND locked_at IS NULL
            RETURNING exercise_id
            """,
            (exercise_id,),
        ).fetchone()
        return row is not None

    def is_locked(self, exercise_id: str) -> bool:
        cfg = self.get(exercise_id)
        return cfg is not None and cfg.locked_at is not None