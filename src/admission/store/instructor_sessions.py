"""教官的瀏覽器 session 儲存（#126 item 2）。

跟 `SeatStore` 的 session 方法（`bind_session`／`revoke_session`／`resolve_session`）
同構，但刻意分開成獨立的 store：教官 session 不綁 `seat_id`（教官不是座位），
綁的是 `actor`——`instructor_tokens` 表裡設定的名字。同一份憑證表因此有兩種
出示方式：伺服器對伺服器的 Authorization Bearer（`api.py` 的 `instructor()`
dependency）與人在瀏覽器裡的表單登入換 cookie（這裡）。兩者驗證同一把鑰匙，
不是兩把。
"""

from __future__ import annotations

import hashlib
import secrets

import psycopg


class InstructorSessionStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def bind(self, actor: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            """INSERT INTO admission_instructor_session(token_hash, actor, expires_at)
               VALUES (%s, %s, now() + (%s * interval '1 second'))""",
            (self._digest(token), actor, ttl_seconds),
        )
        return token

    def resolve(self, token: str | None) -> str | None:
        """回傳這個 session 的 actor 名字；無效／過期／已登出回 None。"""
        if not token:
            return None
        row = self.conn.execute(
            """SELECT actor FROM admission_instructor_session
               WHERE token_hash=%s AND revoked_at IS NULL AND expires_at > now()""",
            (self._digest(token),),
        ).fetchone()
        return row[0] if row else None

    def revoke(self, token: str | None) -> bool:
        if not token:
            return False
        row = self.conn.execute(
            """UPDATE admission_instructor_session SET revoked_at=now()
               WHERE token_hash=%s AND revoked_at IS NULL
               RETURNING token_hash""",
            (self._digest(token),),
        ).fetchone()
        return row is not None

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
