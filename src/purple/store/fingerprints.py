"""Fingerprint → event_id 對映（票 05）。

Grafana 一個告警的 firing 與 resolved 帶同一個 fingerprint。第一次見到某
fingerprint 時鑄造 event_id 並記住；之後（resolved）回同一個 event_id ——
於是 firing 與 resolved 共用同一個 event_id，用 lifecycle 區分（spec §2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from purple.receiver.core import mint_event_id


@dataclass
class FingerprintIndex:
    """Grafana fingerprint → event_id，**限定在一場演練內**。

    `exercise_id` 不是可有可無的分類欄位：Grafana 的 fingerprint 只由 rule 的
    label 集合決定，同一條規則在下一場會再次產生一模一樣的值。沒有場次作用域，
    第二場的 firing 會撈回第一場的 event_id。
    """

    conn: psycopg.Connection
    exercise_id: str

    def event_id_for(self, fingerprint: str) -> str | None:
        row = self.conn.execute(
            """
            SELECT event_id FROM alert_fingerprints
            WHERE exercise_id = %s AND fingerprint = %s
            """,
            (self.exercise_id, fingerprint),
        ).fetchone()
        return row[0] if row else None

    def assign(self, fingerprint: str) -> str:
        """回傳此 fingerprint 的 event_id：首見則鑄造並記住，再見則回原值。

        用 ON CONFLICT DO NOTHING ＋ 回讀，避免兩次 firing 各鑄一個 id。
        """
        candidate = mint_event_id()
        self.conn.execute(
            """
            INSERT INTO alert_fingerprints (exercise_id, fingerprint, event_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (exercise_id, fingerprint) DO NOTHING
            """,
            (self.exercise_id, fingerprint, candidate),
        )
        # 不論剛插入或早已存在，一律回讀真正生效的 event_id。
        existing = self.event_id_for(fingerprint)
        assert existing is not None, "剛寫入的 fingerprint 不該讀不到"
        return existing
