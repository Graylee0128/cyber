"""Fingerprint → event_id 對映（票 05）。

Grafana 一個告警的 firing 與 resolved 帶同一個 fingerprint。第一次見到某
fingerprint 時鑄造 event_id 並記住；之後（resolved）回同一個 event_id ——
於是 firing 與 resolved 共用同一個 event_id，用 lifecycle 區分（spec §2.2）。
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from purple.receiver.core import mint_event_id


@dataclass(frozen=True)
class FingerprintMatch:
    """一個 fingerprint 配對到的 firing：event_id 與**它屬於哪一場**。

    場次要跟著回傳，是因為 resolved 可能在換場之後才到 —— 它屬於 firing 那一場，
    不屬於當下正在跑的那一場。見 `pair_with_firing`。
    """

    event_id: str
    exercise_id: str


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

    def pair_with_firing(self, fingerprint: str) -> FingerprintMatch | None:
        """找出這個 fingerprint 的 firing，**跨場次也找**。

        `resolved` 常常在 firing 之後很久才到 —— 演練結束、下一場已經開始的
        情況是正常的，不是異常。只在當前場次裡找，換場後到的 resolved 會配不到
        firing，於是鑄一個新的 event_id，變成一筆掛在新場次上的孤兒；舊場次的
        firing 則永遠等不到終點，`resolutions_by_event()` 依 exercise_id 過濾，
        含制時間就這樣靜默變成不可得（契約 §2.2）。

        所以：**優先**當前場次（同一場的重送必須配到自己這場），找不到才回退到
        最近一次的對映，並把那一場的 exercise_id 一起帶回去，讓 resolved 歸到
        firing 的場次而不是當下這場。
        """
        row = self.conn.execute(
            """
            SELECT event_id, exercise_id FROM alert_fingerprints
            WHERE fingerprint = %s
            ORDER BY (exercise_id = %s) DESC, created_at DESC
            LIMIT 1
            """,
            (fingerprint, self.exercise_id),
        ).fetchone()
        if row is None:
            return None
        return FingerprintMatch(event_id=row[0], exercise_id=row[1])

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
