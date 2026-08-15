"""票 10 —— 走 metrics 而非 log 的偵測路徑（brute force / T1110）。

Grafana 對 PromQL 告警與對 LogQL 告警，送出的 webhook 形狀相同，
所以同一個 receiver 就能把 metric 告警轉成 Core Event。可驗的契約在這裡。

**環境阻塞（#13）**：OTLP :4317 實際被使用、Prometheus :9090 端到端通，
需要真的 Prometheus / OTLP collector，本機沒有。
"""

from purple.harness import assert_core_event, wait_for_event
from purple.receiver import ingest_alert
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore

import pytest

# 一個以 PromQL 觸發的 brute force 告警（T1110）。annotations 帶的是 PromQL，
# 但 Core Event 一個字都看不到 backend/query —— 那些只進 Alert Record。
GRAFANA_METRIC_WEBHOOK = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-bruteforce-0001",
            "startsAt": "2026-08-08T15:00:00+08:00",
            "labels": {
                "alertname": "SSHBruteForce",
                "event_type": "attack.detected",
                "technique": "T1110",
                "team": "red",
                "severity": "medium",
                "scenario_id": "bruteforce-01",
                "exercise_id": "ex-001",
                "service": "ssh-target",
            },
            "annotations": {
                "query": "rate(ssh_failed_logins_total[1m]) > 5",  # PromQL, 不是 LogQL
                "threshold": "> 5 fails / 1m",
            },
            "values": {"A": 17},
        }
    ],
}


@pytest.fixture
def stores(pg_connection):
    return CoreEventStore(pg_connection), AlertRecordStore(pg_connection)


def test_metric_alert_produces_core_event(stores):
    events, records = stores
    mark = events.now()

    ingest_alert(
        GRAFANA_METRIC_WEBHOOK,
        events=events,
        records=records,
        exercise_id="ex-current",
        scenario_id="bruteforce-01",
    )

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "bruteforce-01",
        what="bruteforce-01 metric Core Event",
        timeout_s=3,
        poll_s=0.1,
    )
    assert_core_event(core)
    assert core["technique"] == "T1110"


def test_promql_stays_out_of_the_core_event(stores):
    """PromQL 是 backend 細節 —— 只進 Alert Record，不進 Core Event。"""
    events, records = stores
    [event_id] = ingest_alert(
        GRAFANA_METRIC_WEBHOOK,
        events=events,
        records=records,
        exercise_id="ex-current",
        scenario_id="bruteforce-01",
    )

    core = events.by_id(event_id)[0]
    assert "promql" not in repr(core).lower()
    assert "rate(" not in repr(core)
    # 但 Alert Record 保有原始 query 供 Engine 取證
    assert "rate(" in records.by_id(event_id)["query"]


def test_t1110_carries_its_interpretation_note():
    """T1110 的判讀限制（需與成功登入證據連結）必須帶到下游。"""
    from purple.receiver.whitelist import default_whitelist

    assert "成功登入" in default_whitelist().note("T1110")
