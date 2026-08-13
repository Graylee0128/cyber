from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

# ---- 藍隊：座位已由 instructor 開場前 bulk-build 好，領號是「鎖既有 free 座位」----

CLAIM_FREE_SEAT = """
UPDATE seat
SET state = 'requested', player_id = %s
WHERE seat_id = (
    SELECT seat_id FROM seat
    WHERE exercise_id = %s AND team = %s AND state = 'free'
    ORDER BY seat_id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING seat_id
"""

# ---- 紅隊：座位當場動態生成，領號是「INSERT 新座位」，上限靠 advisory lock 擋 race ----

#: pg_advisory_xact_lock 的固定 namespace，跟 db.py 的 SCHEMA_LOCK_KEY（0x41444D31）錯開。
RED_POOL_LOCK_NS = 0x52454431  # "RED1"

COUNT_RED_SEATS = """
SELECT count(*) FROM seat WHERE exercise_id = %s AND team = 'red'
"""

INSERT_RED_SEAT = """
INSERT INTO seat (seat_id, exercise_id, team, kind, state, player_id)
VALUES (%s, %s, 'red', 'shell', 'requested', %s)
"""


@dataclass
class SeatStore:
    conn: psycopg.Connection

    # ---------- 藍隊：bulk-build + 自助領號 ----------

    def bulk_build_blue_seats(self, exercise_id: str, count: int) -> list[str]:
        """instructor 開場前依實到人數一次全建（§4.3、決策25）。
        每人一段，kind 固定 shell（§6 已拍板走 shell）。回傳新建的 seat_id 列表。
        """
        seat_ids = [str(uuid.uuid4()) for _ in range(count)]
        with self.conn.transaction():
            for seat_id in seat_ids:
                self.conn.execute(
                    """
                    INSERT INTO seat (seat_id, exercise_id, team, kind, state)
                    VALUES (%s, %s, 'blue', 'shell', 'free')
                    """,
                    (seat_id, exercise_id),
                )
        return seat_ids

    def request_blue_seat(self, exercise_id: str, pool_locked: bool) -> str | None:
        """藍隊自助領號。鎖池後（exercise start）不再接受，呼叫端須先查
        PoolConfigStore.is_locked() 並傳進來——不在此處查表，避免兩個 store
        之間互相依賴（呼叫順序交給 API 層決定，見 request_seat()）。

        成功回傳新鑄造的 player_id；額滿（無 free 座位）或已鎖池回傳 None。
        """
        if pool_locked:
            return None
        player_id = str(uuid.uuid4())
        row = self.conn.execute(
            CLAIM_FREE_SEAT, (player_id, exercise_id, "blue")
        ).fetchone()
        if row is None:
            return None
        return player_id

    # ---------- 紅隊：動態生成，上限用 advisory lock 擋 race ----------

    def request_red_seat(self, exercise_id: str, red_cap: int) -> str | None:
        """紅隊領號：當場 INSERT 新座位。red_cap 是「最多可以動態長到幾個座位」，
        不是預建數量（§4.3）。用 pg_advisory_xact_lock 包住「數數 + INSERT」，
        避免併發請求同時通過計數檢查、一起塞進去導致超過上限。
        pg_advisory_xact_lock 是 transaction-scoped，transaction 結束（commit/rollback）
        會自動解鎖，就算中途拋例外也不會忘記解鎖。
        """
        player_id = str(uuid.uuid4())
        with self.conn.transaction():
            self.conn.execute(
                "SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                (RED_POOL_LOCK_NS, exercise_id),
            )
            count = self.conn.execute(COUNT_RED_SEATS, (exercise_id,)).fetchone()[0]
            if count >= red_cap:
                return None
            seat_id = str(uuid.uuid4())
            self.conn.execute(INSERT_RED_SEAT, (seat_id, exercise_id, player_id))
        return player_id

    # ---------- 統一入口：依 team 分流 ----------

    def request_seat(
        self, exercise_id: str, team: str, red_cap: int, pool_locked: bool
    ) -> str | None:
        """領號的統一入口。呼叫端（api.py）先從 PoolConfigStore 拿到 red_cap 與
        pool_locked（is_locked），再呼叫這裡——刻意不讓 SeatStore 直接依賴
        PoolConfigStore，兩個 store 保持各自對應各自的 table，好測、好懂。
        """
        if team == "blue":
            return self.request_blue_seat(exercise_id, pool_locked)
        elif team == "red":
            return self.request_red_seat(exercise_id, red_cap)
        else:
            raise ValueError(f"unknown team: {team!r}")

    # ---------- 通用座位生命週期操作（不分紅藍）----------

    def mark_ready(self, seat_id: str, endpoints: list[dict[str, Any]]) -> bool:
        """provisioner 回報座位已就緒，寫入 endpoints 並轉 state=ready（§4.4）。"""
        row = self.conn.execute(
            """
            UPDATE seat SET state = 'ready', endpoints = %s
            WHERE seat_id = %s AND state = 'requested'
            RETURNING seat_id
            """,
            (Jsonb(endpoints), seat_id),
        ).fetchone()
        return row is not None

    def mark_failed(self, seat_id: str) -> bool:
        """逾時三段式的第一段：超過 T 秒標 failed（§4.4，具體 T 值待 #78）。"""
        row = self.conn.execute(
            """
            UPDATE seat SET state = 'failed'
            WHERE seat_id = %s AND state = 'requested'
            RETURNING seat_id
            """,
            (seat_id,),
        ).fetchone()
        return row is not None

    def release(self, seat_id: str) -> bool:
        """釋放座位（instructor 操作之一，§7.1）：player_id 作廢、分數歸零、
        seat 回 free。藍池釋放後需要手動重建（不在此函式處理，§4.3 交代
        「藍池則需手動重建」——重建走 bulk_build_blue_seats 或未來的單一補建方法）。
        """
        row = self.conn.execute(
            """
            UPDATE seat SET state = 'free', player_id = NULL, claimed_at = NULL
            WHERE seat_id = %s
            RETURNING seat_id
            """,
            (seat_id,),
        ).fetchone()
        return row is not None

    def rebind_session(self, seat_id: str) -> bool:
        """重新綁定 session（instructor 操作之二，§7.1）：player_id 與分數不變，
        只換裝置。這裡只確認 seat 存在且狀態允許重綁；實際 session token 的
        產生/寫入屬於③（session ↔ seat 綁定），不在 seats.py 範圍。
        """
        row = self.conn.execute(
            """
            SELECT seat_id FROM seat
            WHERE seat_id = %s AND state IN ('requested', 'ready', 'claimed')
            """,
            (seat_id,),
        ).fetchone()
        return row is not None

    def get(self, seat_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT seat_id, exercise_id, team, kind, endpoints, state, player_id, claimed_at
            FROM seat WHERE seat_id = %s
            """,
            (seat_id,),
        ).fetchone()
        if row is None:
            return None
        keys = [
            "seat_id", "exercise_id", "team", "kind",
            "endpoints", "state", "player_id", "claimed_at",
        ]
        return dict(zip(keys, row))

    def pool_snapshot(self, exercise_id: str, team: str) -> dict[str, int]:
        """座位池總覽用（中控 UI §1.3 第一區塊）：各 state 各幾張。"""
        rows = self.conn.execute(
            "SELECT state, count(*) FROM seat WHERE exercise_id = %s AND team = %s GROUP BY state",
            (exercise_id, team),
        ).fetchall()
        return dict(rows)