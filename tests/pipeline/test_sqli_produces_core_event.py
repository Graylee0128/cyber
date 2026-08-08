"""票 02b — 第一條紅燈，票 03 讓它變綠。

spec 的核心契約寫成一條端到端測試：一個代表 SQLi 偵測的 Grafana 告警，
經 receiver 後應出現一筆符合 schema 的 Core Event。

無 Docker 的機器上，Grafana／Loki 中段以「手餵一個代表性 webhook」模擬 ——
map.md 已把 02b／03 列為契約測試（只能對管路行為斷言）。真實 Falco／Loki
鏈路在 #8 起以真環境驗。
"""

from __future__ import annotations

import pytest  # noqa: F401  (fixture 使用)

from purple.harness import assert_core_event, wait_for_event
from purple.receiver import ingest_alert
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore

# 代表一次 SQLi 偵測的 Grafana 統一告警 webhook（firing）。
GRAFANA_SQLI_WEBHOOK = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-sqli-0001",
            "startsAt": "2026-08-08T14:30:00+08:00",
            "labels": {
                "alertname": "SQLInjectionBurst",
                "event_type": "attack.detected",
                "technique": "T1190",
                "team": "red",
                "severity": "high",
                "scenario_id": "sqli-01",
                "exercise_id": "ex-001",
                "service": "vulnerable-app",
                # rule 作者惡意想讓紅隊看不到 —— 必須被 receiver 忽略（visibility 由 event_type 決定）
                "visibility": "instructor",
            },
            "annotations": {
                "query": '{app="vulnerable-app"} |= "OR 1=1" | count_over_time([1m])',
                "threshold": "> 4 req / 1m",
            },
            "values": {"A": 12},
        }
    ],
}


@pytest.fixture
def stores(pg_connection):
    return CoreEventStore(pg_connection), AlertRecordStore(pg_connection)


def test_sqli_produces_core_event(stores):
    events, records = stores
    mark = events.now()

    ingest_alert(GRAFANA_SQLI_WEBHOOK, events=events, records=records)

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01",
        what="sqli-01 attack.detected Core Event",
        timeout_s=3,
        poll_s=0.1,
    )

    # 符合契約：欄位齊全、無 evidence_ref、全文無 backend 字樣
    assert_core_event(core)

    for field in (
        "event_id", "exercise_id", "scenario_id", "event_type", "lifecycle",
        "technique", "visibility", "observed_at", "source", "team", "target",
    ):
        assert field in core, f"缺少必要欄位 {field}"

    assert core["event_type"] == "attack.detected"
    assert core["technique"] == "T1190"
    # rule 想標 instructor，但 attack.detected 一律 public（spec §2.4）
    assert core["visibility"] == "public"
    assert "evidence_ref" not in core
