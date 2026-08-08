"""端到端部署測試（票 02b/03 的真實版本）—— 需要 docker compose 全棧。

02b/03 的契約測試是手餵 webhook；這裡打真的 SQLi，讓它自己走完：
    vulnerable-app → Alloy → Loki → Grafana（唯一 alert engine）→ webhook
    → P1 receiver → PostgreSQL 的 Core Event

只在 integration workflow 跑（`-m integration`）。單元 CI 不含它。
"""

from __future__ import annotations

import os

import pytest

from purple.harness import assert_core_event, wait_for_event
from purple.harness.attacker import inject_sqli
from purple.store.events import CoreEventStore

pytestmark = pytest.mark.integration

APP_URL = os.environ.get("PURPLE_APP_URL", "http://localhost:8080")

# Grafana eval interval 10s + provisioning 起步 + Loki ingestion 延遲 → 給寬裕的窗。
E2E_TIMEOUT_S = 90.0


@pytest.fixture
def events(pg_connection):
    return CoreEventStore(pg_connection)


def test_real_sqli_becomes_a_core_event(events):
    mark = events.now()

    # 打幾次，確保跨過 Loki 的 count_over_time 窗與 Grafana 的 eval 週期。
    for _ in range(5):
        result = inject_sqli(APP_URL, path="/login", param="username")
        assert result.status_code in (200, 401)

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01" and e["event_type"] == "attack.detected",
        what="真實 SQLi 走完管線後的 attack.detected Core Event",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )

    assert_core_event(core)
    assert core["technique"] == "T1190"
    assert core["visibility"] == "public"     # rule 無法覆寫
    assert "evidence_ref" not in core          # 契約：遙測細節不進 Core Event
    assert "loki" not in repr(core).lower()
