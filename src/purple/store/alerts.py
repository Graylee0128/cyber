"""P1 Alert Record 儲存 —— 遙測細節唯一合法的存放處（spec §4.3）。

只有 Evaluation Engine 讀它。`backend` 欄位只存在於這裡：換掉 Loki 時，
改的是查詢實作與這份紀錄，Core Event Schema 不動。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

INSERT = """
INSERT INTO alert_records
    (event_id, grafana_rule, query, threshold, fired_values, labels, backend)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id) DO NOTHING
"""


@dataclass
class AlertRecordStore:
    conn: psycopg.Connection

    def write(self, record: dict[str, Any]) -> None:
        self.conn.execute(
            INSERT,
            (
                record["event_id"],
                record["grafana_rule"],
                record["query"],
                record.get("threshold"),
                Jsonb(record.get("fired_values", [])),
                Jsonb(record.get("labels", {})),
                record["backend"],
            ),
        )

    def by_id(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT event_id, grafana_rule, query, threshold, fired_values, labels, backend "
            "FROM alert_records WHERE event_id = %s",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        keys = ("event_id", "grafana_rule", "query", "threshold", "fired_values", "labels", "backend")
        return dict(zip(keys, row))

    def exists(self, event_id: str) -> bool:
        return (
            self.conn.execute(
                "SELECT 1 FROM alert_records WHERE event_id = %s", (event_id,)
            ).fetchone()
            is not None
        )
