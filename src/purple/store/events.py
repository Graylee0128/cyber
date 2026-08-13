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
    (event_id, lifecycle, event_type, exercise_id, scenario_id, observed_at, action_id, event)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id, lifecycle) DO NOTHING
RETURNING event_id
"""

#: 算「這次動作被偵測到了」的事件型別。`detection.miss` 顯然不算；`response.*`
#: 是反應不是偵測；`exercise.*` 是流程事件。這份清單是判準本身，不得在呼叫端
#: 各自用字串比對重寫一次。
DETECTION_EVENT_TYPES = ("attack.detected", "detection.hit")


@dataclass
class CoreEventStore:
    conn: psycopg.Connection

    def append(self, event: dict[str, Any]) -> bool:
        """寫入一筆事件。同 event_id ＋ lifecycle 重送不會產生第二筆 ——
        Grafana webhook 會重試，重試不該讓 coverage 分子灌水。

        回傳這次是不是真的新插入（而非被 ON CONFLICT DO NOTHING 吃掉的重複）。
        呼叫端（ingest_alert）靠這個值決定要不要把 response 命令放進佇列 ——
        `repeat_interval` 讓 Grafana 對同一個持續 firing 的 alert 反覆重送 webhook
        （見 deploy/grafana/provisioning/alerting/policies.yaml），若每次重送都
        enqueue，同一次攻擊會被重複封鎖、重複產生 response.executed。"""
        row = self.conn.execute(
            INSERT,
            (
                event["event_id"],
                event["lifecycle"],
                event["event_type"],
                event["exercise_id"],
                event["scenario_id"],
                event["observed_at"],
                # 契約保證這個鍵一定在（schema.REQUIRED_FIELDS）。用 [] 而非
                # .get() 是刻意的：漏帶要在寫入時就炸，不要靜靜寫成 NULL。
                event["action_id"],
                Jsonb(event),
            ),
        ).fetchone()
        return row is not None

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

    def detections_by_action(self, exercise_id: str) -> dict[str, list[dict[str, Any]]]:
        """一場演練裡，每個 `action_id` 對應到的偵測事件（#90 Phase 2）。

        `action_id IS NULL` 的事件不會出現在任何一組裡 —— 沒帶關聯鍵的事件
        不得被拿去對應任何註冊動作，這正是本方法只走 `action_id` 的理由。
        技法與時間窗鄰近性在這裡完全不參與。
        """
        rows = self.conn.execute(
            "SELECT action_id, event FROM core_events "
            "WHERE exercise_id = %s AND action_id IS NOT NULL "
            "AND event_type = ANY(%s) ORDER BY recorded_at",
            (exercise_id, list(DETECTION_EVENT_TYPES)),
        ).fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for action_id, event in rows:
            grouped.setdefault(action_id, []).append(event)
        return grouped

    def alert_volume(self, exercise_id: str) -> int:
        """一場演練的告警總量。

        只數 `firing`：`resolved` 與 firing 共用 event_id（spec §2.2），兩筆都數
        會讓每個結束的告警被算成兩次。這個數字**不進任何比率的分母** ——
        它是拿來對照涵蓋率的獨立量，用來看出「涵蓋率很高但告警爆量」。
        """
        return self.conn.execute(
            "SELECT count(*) FROM core_events WHERE exercise_id = %s AND lifecycle = 'firing'",
            (exercise_id,),
        ).fetchone()[0]

    def count(self) -> int:
        return self.conn.execute("SELECT count(*) FROM core_events").fetchone()[0]

    def now(self) -> datetime:
        """資料庫的時間，不是本機的。載具用它取「攻擊開始」的時刻 ——
        用本機時鐘會把票 01 想消滅的偏差重新引進來。"""
        return self.conn.execute("SELECT now()").fetchone()[0]
