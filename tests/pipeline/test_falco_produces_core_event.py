"""票 #9 契約測試 —— Falco 偵測經 Grafana rule → Core Event（T1059）。

與 test_sqli 同法：無 Docker 機器上以「手餵一個代表性 Grafana webhook」模擬
Falco→Alloy→Loki→Grafana 中段。真實 Falco/Loki 鏈路由 `-m integration` 的
tests/integration/test_falco_pipeline.py 在真環境驗。

這裡證的是 **receiver 對 T1059 這條路徑**：Falco 的偵測（經 Grafana）落成一筆
符合契約的 attack.detected Core Event，technique=T1059，visibility 由 event_type
決定（rule 無法覆寫）。
"""

from __future__ import annotations

import pytest

from purple.harness import assert_core_event, wait_for_event
from purple.receiver import ingest_alert
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore

# 代表一次 Falco T1059 偵測的 Grafana 統一告警 webhook（firing）。
GRAFANA_FALCO_WEBHOOK = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-falco-exec-0001",
            "startsAt": "2026-08-09T14:30:00+08:00",
            "labels": {
                "alertname": "FalcoCommandExec",
                "event_type": "attack.detected",
                "technique": "T1059",
                "team": "red",
                "severity": "high",
                "scenario_id": "falco-exec-01",
                "exercise_id": "ex-001",
                "service": "vulnerable-app",
                # rule 作者想藏給教官看 —— 必須被忽略（visibility 由 event_type 決定）
                "visibility": "instructor",
            },
            "annotations": {
                "query": '{job="falco"} |= "PurpleScope exec detected"',
                "threshold": "> 0 / 1m",
            },
            "values": {"A": 3},
        }
    ],
}


@pytest.fixture
def stores(pg_connection):
    return CoreEventStore(pg_connection), AlertRecordStore(pg_connection)


def test_falco_exec_produces_core_event(stores):
    events, records = stores
    mark = events.now()

    ingest_alert(
        GRAFANA_FALCO_WEBHOOK,
        events=events,
        records=records,
        exercise_id="ex-current",
    )

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "falco-exec-01",
        what="falco-exec-01 attack.detected Core Event",
        timeout_s=3,
        poll_s=0.1,
    )

    assert_core_event(core)
    assert core["event_type"] == "attack.detected"
    assert core["technique"] == "T1059"           # Falco 走 syscall 路徑，非 log/metric
    assert core["visibility"] == "public"          # attack.detected 一律 public（rule 無法覆寫）
    assert "evidence_ref" not in core
    assert "loki" not in repr(core).lower()        # 無 backend 洩漏（backend 只該進 Alert Record）
    assert core["source"] == "grafana"
