from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from admission.store.pool import POOL_LOCK_NS

# ---- 藍隊：座位已由 instructor 開場前 bulk-build 好，領號是「鎖既有 free 座位」----

CLAIM_FREE_SEAT = """
UPDATE seat
SET state = 'requested', player_id = %s, requested_at = now(), retry_count = 0
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
RED_POOL_LOCK_NS = POOL_LOCK_NS

COUNT_RED_SEATS = """
SELECT count(*) FROM seat WHERE exercise_id = %s AND team = 'red' AND state <> 'released'
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

    def request_blue_seat(self, exercise_id: str, pool_locked: bool = False) -> dict[str, str] | None:
        """藍隊自助領取已預建的 free 座位。

        ``pool_locked`` remains only for source compatibility; the database
        config row is checked under a lock in the same transaction as the
        seat claim. This prevents stale application state from admitting a
        claim while an instructor is locking the pool.
        """
        del pool_locked
        player_id = str(uuid.uuid4())
        with self.conn.transaction():
            config = self.conn.execute(
                """SELECT locked_at FROM exercise_pool_config
                   WHERE exercise_id=%s FOR SHARE""",
                (exercise_id,),
            ).fetchone()
            if config is None or config[0] is not None:
                return None
            row = self.conn.execute(
                CLAIM_FREE_SEAT, (player_id, exercise_id, "blue")
            ).fetchone()
        if row is None:
            return None
        return {"seat_id": row[0], "player_id": player_id}

    # ---------- 紅隊：動態生成，上限用 advisory lock 擋 race ----------

    def request_red_seat(self, exercise_id: str, red_cap: int | None = None) -> dict[str, str] | None:
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
            row = self.conn.execute(
                """SELECT red_cap FROM exercise_pool_config
                   WHERE exercise_id=%s FOR UPDATE""", (exercise_id,)
            ).fetchone()
            if row is None:
                return None
            authoritative_cap = row[0]
            count = self.conn.execute(COUNT_RED_SEATS, (exercise_id,)).fetchone()[0]
            if count >= authoritative_cap:
                return None
            seat_id = str(uuid.uuid4())
            self.conn.execute(INSERT_RED_SEAT, (seat_id, exercise_id, player_id))
            self.conn.execute("UPDATE seat SET requested_at=now() WHERE seat_id=%s", (seat_id,))
        return {"seat_id": seat_id, "player_id": player_id}

    # ---------- 統一入口：依 team 分流 ----------

    def request_seat(
        self, exercise_id: str, team: str, red_cap: int, pool_locked: bool
    ) -> dict[str, str] | None:
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

    def mark_published(self, seat_id: str) -> None:
        self.conn.execute(
            "UPDATE seat SET published_at=COALESCE(published_at, now()) WHERE seat_id=%s",
            (seat_id,),
        )

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
        seat 進入 released。藍池釋放後需要手動重建（不在此函式處理，§4.3 交代
        「藍池則需手動重建」——重建走 bulk_build_blue_seats 或未來的單一補建方法）。
        """
        row = self.conn.execute(
            """
            UPDATE seat SET state = 'released', player_id = NULL, claimed_at = NULL,
                            requested_at = NULL, published_at = NULL
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
            SELECT seat_id, exercise_id, team, kind, endpoints, state, player_id, claimed_at,
                   requested_at, retry_count, published_at
            FROM seat WHERE seat_id = %s
            """,
            (seat_id,),
        ).fetchone()
        if row is None:
            return None
        keys = [
            "seat_id", "exercise_id", "team", "kind",
            "endpoints", "state", "player_id", "claimed_at", "requested_at",
            "retry_count", "published_at",
        ]
        return dict(zip(keys, row))

    def get_for_update(self, seat_id: str) -> dict[str, Any] | None:
        self.conn.execute("SELECT seat_id FROM seat WHERE seat_id=%s FOR UPDATE", (seat_id,))
        return self.get(seat_id)

    def list_requested(self, team: str | None = None) -> list[dict[str, Any]]:
        """#62：provisioner 輪詢用——列出所有還在等建置的座位。

        跨 exercise（provisioner 是 host 級服務，不綁單一場次）。只回建置需要的
        欄位，不含 endpoints/player_id 這些跟「要不要幫它建容器」無關的狀態——
        provisioner 不該因為多看了不需要的欄位而長出額外的判斷分支。
        """
        query = "SELECT seat_id, exercise_id, team, kind FROM seat WHERE state = 'requested'"
        params: tuple[Any, ...] = ()
        if team is not None:
            query += " AND team = %s"
            params = (team,)
        query += " ORDER BY requested_at"
        rows = self.conn.execute(query, params).fetchall()
        return [
            {"seat_id": r[0], "exercise_id": r[1], "team": r[2], "kind": r[3]}
            for r in rows
        ]

    def list_active(self, team: str | None = None) -> list[str]:
        """#62：provisioner 孤兒回收用——列出「還算數」的座位 id（requested／
        ready／claimed，尚未 released／failed）。跟 `list_requested` 分開是刻意
        的：孤兒判定要問的是「這個容器還有沒有對應的座位」，不是「這個座位
        還要不要建」，兩者答案不一樣——已經 `ready` 的座位不需要建，但拆掉它
        的容器仍然是誤刪一個正在用的座位。"""
        query = "SELECT seat_id FROM seat WHERE state IN ('requested','ready','claimed')"
        params: tuple[Any, ...] = ()
        if team is not None:
            query += " AND team = %s"
            params = (team,)
        return [r[0] for r in self.conn.execute(query, params).fetchall()]

    def pool_snapshot(self, exercise_id: str, team: str) -> dict[str, int]:
        """座位池總覽用（中控 UI §1.3 第一區塊）：各 state 各幾張。"""
        rows = self.conn.execute(
            "SELECT state, count(*) FROM seat WHERE exercise_id = %s AND team = %s GROUP BY state",
            (exercise_id, team),
        ).fetchall()
        return dict(rows)

    def issue_remote_link(self, exercise_id: str, ttl_seconds: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        link_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO admission_remote_link
                   (link_id,token_hash,exercise_id,expires_at)
               VALUES (%s,%s,%s,now() + (%s * interval '1 second'))""",
            (link_id, self._digest(token), exercise_id, ttl_seconds),
        )
        return link_id, token

    def consume_remote_link(self, exercise_id: str, token: str) -> bool:
        row = self.conn.execute(
            """UPDATE admission_remote_link SET used_at=now()
               WHERE token_hash=%s AND exercise_id=%s AND used_at IS NULL
                 AND revoked_at IS NULL AND expires_at > now()
               RETURNING token_hash""",
            (self._digest(token), exercise_id),
        ).fetchone()
        return row is not None

    def revoke_remote_link(self, link_id: str, actor: str) -> bool:
        with self.conn.transaction():
            row = self.conn.execute(
                """UPDATE admission_remote_link SET revoked_at=now()
                   WHERE link_id=%s AND used_at IS NULL AND revoked_at IS NULL
                   RETURNING exercise_id""",
                (link_id,),
            ).fetchone()
            if row is None:
                return False
            self.conn.execute(
                """INSERT INTO admission_audit(actor,seat_id,action)
                   VALUES (%s,%s,'revoke_remote_link')""",
                (actor, f"remote-link:{link_id}"),
            )
        return True

    def bind_session(self, seat_id: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            """INSERT INTO admission_session(token_hash,seat_id,expires_at)
               VALUES (%s,%s,now() + (%s * interval '1 second'))""",
            (self._digest(token), seat_id, ttl_seconds),
        )
        return token

    def revoke_session(self, token: str) -> bool:
        row = self.conn.execute(
            """UPDATE admission_session SET revoked_at=now()
               WHERE token_hash=%s AND revoked_at IS NULL AND expires_at > now()
               RETURNING seat_id""",
            (self._digest(token),),
        ).fetchone()
        return row is not None

    def revoke_sessions(self, seat_id: str) -> None:
        self.conn.execute(
            "UPDATE admission_session SET revoked_at=now() WHERE seat_id=%s AND revoked_at IS NULL",
            (seat_id,),
        )

    def resolve_session(self, token: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT s.seat_id FROM admission_session x JOIN seat s ON s.seat_id=x.seat_id
               WHERE x.token_hash=%s AND x.revoked_at IS NULL
                 AND x.expires_at > now()
                 AND s.state IN ('requested','ready','claimed')""",
            (self._digest(token),),
        ).fetchone()
        return self.get(row[0]) if row else None

    def retry_failed(self, seat_id: str) -> bool:
        row = self.conn.execute(
            """UPDATE seat SET state='requested', requested_at=now()
               WHERE seat_id=%s AND state='failed' AND retry_count=1 RETURNING seat_id""",
            (seat_id,),
        ).fetchone()
        return row is not None

    def claim_for_access(self, seat_id: str) -> None:
        self.conn.execute(
            "UPDATE seat SET state='claimed', claimed_at=COALESCE(claimed_at,now()) WHERE seat_id=%s AND state='ready'",
            (seat_id,),
        )

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
