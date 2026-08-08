"""端到端部署測試 —— 需要 docker compose 全棧（`-m integration`）。

契約測試（tests/pipeline）是手餵 webhook；這裡打真的請求，讓它自己走完：
    vulnerable-app → Alloy → Loki / Prometheus → Grafana（唯一 alert engine）
    → webhook → P1 receiver → PostgreSQL

測試在檔案內的**順序有意義**：負向先跑（此時 Loki 還沒有任何 SQLi 記錄），
resolved 的慢測試放最後。
"""

from __future__ import annotations

import os

import pytest

from purple.harness import assert_core_event, wait_for_event
from purple.harness.attacker import inject_sqli
from purple.harness.waiting import EventNotSeen
from purple.store.events import CoreEventStore

pytestmark = pytest.mark.integration

APP_URL = os.environ.get("PURPLE_APP_URL", "http://localhost:8080")

# Grafana eval 10s + provisioning 起步 + Loki ingestion 延遲 → 給寬裕的窗。
E2E_TIMEOUT_S = 90.0
# resolved 要等 count_over_time([1m]) 掉回 0，天生慢。
RESOLVE_TIMEOUT_S = 150.0


@pytest.fixture
def events(pg_connection):
    return CoreEventStore(pg_connection)


def _normal_login(username: str = "alice") -> None:
    """打一次正常登入。app 對登入失敗回 401 —— urlopen 會把 4xx 當例外拋，
    這裡吞掉：請求送達就算數（與 inject_sqli 對 4xx 的處理一致）。"""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{APP_URL}/login?username={username}", timeout=5) as r:
            r.read()
    except urllib.error.HTTPError:
        pass  # 401 = 送達了但登入失敗，正是預期


def test_normal_login_does_not_trigger_sqli_detection(events):
    """負向：正常登入**不該**產生 SQLi 偵測事件。

    證明 SQLInjectionBurst 規則不是「永遠開火」—— 少了這條，一個恆真的壞規則
    也會讓 happy path 綠。範圍限 sqli-01，因此不受 brute force 規則影響。
    先跑：此時 Loki 尚無任何 sqli_suspected=true 記錄。
    """
    mark = events.now()
    for _ in range(3):  # 少量請求，不跨過 brute force 門檻
        _normal_login()

    # 等兩個 eval 週期，確認這段時間內沒有 sqli-01 事件冒出來。
    with pytest.raises(EventNotSeen):
        wait_for_event(
            fetch=lambda: events.since(mark),
            match=lambda e: e["scenario_id"] == "sqli-01",
            what="不該出現的 sqli-01 事件",
            timeout_s=25,
            poll_s=2.0,
        )


def test_real_sqli_becomes_a_core_event(events):
    """happy path：真 SQLi 走完 log 管線 → attack.detected Core Event。"""
    mark = events.now()
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
    assert "evidence_ref" not in core
    assert "loki" not in repr(core).lower()


def test_bruteforce_metric_becomes_a_core_event(events):
    """metric 路徑：大量失敗登入 → Prometheus → Grafana PromQL 告警 → Core Event。

    證明 :9090 路徑真的通，不只是單元契約。"""
    mark = events.now()
    for _ in range(20):  # 跨過 brute force 門檻（> 10 / 1m）
        _normal_login("attacker")

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "bruteforce-01" and e["event_type"] == "attack.detected",
        what="brute force metric 走完後的 attack.detected Core Event",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    assert_core_event(core)
    assert core["technique"] == "T1110"


def test_sqli_alert_resolves_after_attack_stops(events):
    """lifecycle：攻擊停止後 Grafana 送 resolved，與 firing 共用同一 event_id。

    慢測試（放最後）：resolved 要等 Loki 的 count_over_time([1m]) 掉回 0。"""
    mark = events.now()
    for _ in range(5):
        inject_sqli(APP_URL, path="/login", param="username")

    firing = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01" and e["lifecycle"] == "firing",
        what="firing（lifecycle 測試）",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    firing_id = firing["event_id"]

    # 停止攻擊。等 SQLi 記錄從 1 分鐘窗中老化，Grafana 才會 resolve。
    resolved = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["event_id"] == firing_id and e["lifecycle"] == "resolved",
        what="resolved（與 firing 共用 event_id）",
        timeout_s=RESOLVE_TIMEOUT_S,
        poll_s=3.0,
    )
    assert resolved["event_id"] == firing_id
    assert resolved["lifecycle"] == "resolved"
