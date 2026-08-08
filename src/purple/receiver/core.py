"""Receiver 的 pure core —— 純函數、無 I/O，可不啟 docker 單元測試。

把一個 Grafana 告警的一筆 alert 轉成兩份紀錄：Core Event（遊戲語意）與
P1 Alert Record（遙測細節）。兩份用同一個 `event_id` 對接。

刻意分離的理由（票 03 設計決定）：沒有測試施力面的話，這些邏輯會長進
HTTP handler 裡再也拆不出來。
"""

from __future__ import annotations

import uuid
from typing import Any

from purple.harness.schema import assert_core_event, expected_visibility

#: Grafana webhook 的 alert status → Core Event lifecycle。
#: `pending` 不在表內 → 不產生 Core Event（spec §2.2）。
LIFECYCLE_BY_STATUS = {
    "firing": "firing",
    "resolved": "resolved",
}

#: 目前的遙測後端。只寫進 Alert Record，永不進 Core Event。
BACKEND = "loki"


def mint_event_id() -> str:
    """join key 的唯一產生來源（spec §2.5）。receiver 鑄造，兩份紀錄共用。"""
    return "evt-" + uuid.uuid4().hex


def lifecycle_of(alert: dict[str, Any]) -> str | None:
    """回傳 lifecycle；status 不產生事件（如 pending）時回 None。"""
    return LIFECYCLE_BY_STATUS.get(alert.get("status", ""))


def build_core_event(alert: dict[str, Any], event_id: str, lifecycle: str) -> dict[str, Any]:
    """組出符合契約的 Core Event。

    visibility 由 `event_type` 對照表決定，**忽略** rule 自帶的 visibility label ——
    否則寫規則的人就能決定紅隊看得到什麼（spec §2.4）。
    """
    labels = alert["labels"]
    event_type = labels["event_type"]

    event = {
        "event_id": event_id,
        "exercise_id": labels["exercise_id"],
        "scenario_id": labels["scenario_id"],
        "event_type": event_type,
        "lifecycle": lifecycle,
        "severity": labels["severity"],
        "source": "grafana",
        "team": labels["team"],
        "technique": labels["technique"],
        "target": {"service": labels["service"]},
        "observed_at": alert["startsAt"],
        # 由對照表推導，不讀 labels["visibility"]
        "visibility": expected_visibility(event_type),
    }

    # 防禦性：離開 core 前就保證合約。不合法的東西不該有機會落地。
    assert_core_event(event)
    return event


def build_alert_record(alert: dict[str, Any], event_id: str) -> dict[str, Any]:
    """遙測細節。只有 Evaluation Engine 讀，是 backend 欄位唯一合法的家。"""
    labels = alert["labels"]
    annotations = alert.get("annotations", {})
    return {
        "event_id": event_id,
        "grafana_rule": labels["alertname"],
        "query": annotations.get("query", ""),
        "threshold": annotations.get("threshold"),
        "fired_values": alert.get("values", {}),
        "labels": labels,
        "backend": BACKEND,
    }
