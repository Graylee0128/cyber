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
import uuid

import psycopg


POOL_LOCK_NS = 0x504F4F4C  # "POOL"


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

    def set_caps_and_prepare_blue(
        self, exercise_id: str, red_cap: int, blue_cap: int
    ) -> None:
        """Configure caps and create unprovisioned blue *database slots*.

        This is the public setup operation. It is serialized with
        ``lock_and_build_blue`` so preparation cannot cross the start lock.
        These free rows deliberately have empty endpoints: they are capacity
        reservations, not machines. Lock/start is the signal for the external
        #62 provisioner to build every slot and later report endpoints.
        Existing seats are retained; lowering below the already-built count
        is rejected because deleting a potentially claimed seat is unsafe.
        """
        with self.conn.transaction():
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (POOL_LOCK_NS, exercise_id),
            )
            self.set_caps(exercise_id, red_cap, blue_cap)
            row = self.conn.execute(
                """SELECT blue_cap, locked_at FROM exercise_pool_config
                   WHERE exercise_id=%s FOR UPDATE""",
                (exercise_id,),
            ).fetchone()
            if row is None or row[1] is not None:
                raise RuntimeError("pool configuration is locked")
            existing = self.conn.execute(
                "SELECT count(*) FROM seat WHERE exercise_id=%s AND team='blue'",
                (exercise_id,),
            ).fetchone()[0]
            if existing > row[0]:
                raise ValueError("blue_cap cannot be lower than the prepared seat count")
            active_red = self.conn.execute(
                """SELECT count(*) FROM seat
                   WHERE exercise_id=%s AND team='red' AND state<>'released'""",
                (exercise_id,),
            ).fetchone()[0]
            if active_red > red_cap:
                raise ValueError("red_cap cannot be lower than the allocated seat count")
            self._insert_blue(exercise_id, row[0] - existing)

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

    def lock_and_build_blue(self, exercise_id: str) -> bool:
        """Fill missing DB slots and lock the configured blue pool once.

        The advisory transaction lock serializes callers even before either
        one reaches the config row. The row lock then makes the config's
        ``locked_at`` value authoritative for the check/build/lock sequence.
        No container or endpoint is created here; the external provisioner
        observes the locked slots and owns that deployment work.
        """
        with self.conn.transaction():
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (POOL_LOCK_NS, exercise_id),
            )
            row = self.conn.execute(
                """SELECT blue_cap, locked_at FROM exercise_pool_config
                   WHERE exercise_id=%s FOR UPDATE""",
                (exercise_id,),
            ).fetchone()
            if row is None:
                raise KeyError(exercise_id)
            blue_cap, locked_at = row
            if locked_at is not None:
                return False
            existing = self.conn.execute(
                "SELECT count(*) FROM seat WHERE exercise_id=%s AND team='blue'",
                (exercise_id,),
            ).fetchone()[0]
            if existing > blue_cap:
                raise ValueError("prepared blue seats exceed blue_cap")
            self._insert_blue(exercise_id, blue_cap - existing)
            self.conn.execute(
                "UPDATE exercise_pool_config SET locked_at=now() WHERE exercise_id=%s",
                (exercise_id,),
            )
        return True

    def _insert_blue(self, exercise_id: str, count: int) -> None:
        for _ in range(count):
            self.conn.execute(
                """INSERT INTO seat (seat_id, exercise_id, team, kind, state)
                   VALUES (%s, %s, 'blue', 'shell', 'free')""",
                (str(uuid.uuid4()), exercise_id),
            )

    def is_locked(self, exercise_id: str) -> bool:
        cfg = self.get(exercise_id)
        return cfg is not None and cfg.locked_at is not None

    def availability(self, exercise_id: str) -> dict | None:
        cfg = self.get(exercise_id)
        if cfg is None:
            return None
        red_used = self.conn.execute(
            """SELECT count(*) FROM seat
               WHERE exercise_id=%s AND team='red' AND state<>'released'""",
            (exercise_id,),
        ).fetchone()[0]
        blue_free = self.conn.execute(
            """SELECT count(*) FROM seat
               WHERE exercise_id=%s AND team='blue' AND state='free'""",
            (exercise_id,),
        ).fetchone()[0]
        red_remaining = max(0, cfg.red_cap - red_used)
        blue_locked = cfg.locked_at is not None
        return {
            "exercise_id": exercise_id,
            "teams": {
                "red": {
                    "remaining": red_remaining,
                    "disabled": red_remaining == 0,
                    "reason": "full" if red_remaining == 0 else None,
                },
                "blue": {
                    "remaining": 0 if blue_locked else blue_free,
                    "disabled": blue_locked or blue_free == 0,
                    "reason": "locked" if blue_locked else ("full" if blue_free == 0 else None),
                },
            },
        }
