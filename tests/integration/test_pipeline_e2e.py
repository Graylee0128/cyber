"""端到端部署測試 —— 需要 docker compose 全棧（`-m integration`）。

契約測試（tests/pipeline）是手餵 webhook；這裡打真的請求，讓它自己走完：
    vulnerable-app → Alloy → Loki / Prometheus → Grafana（唯一 alert engine）
    → webhook → P1 receiver → PostgreSQL

測試在檔案內的**順序有意義**：負向先跑（此時 Loki 還沒有任何 SQLi 記錄），
resolved 的慢測試放最後。

每條測試會 echo 出每個階段與觀察到的事件欄位（workflow 用 -v -s 顯示），
方便逐條在 CI log 對照。
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

E2E_TIMEOUT_S = 90.0
RESOLVE_TIMEOUT_S = 150.0


# --- echo helpers：讓每一步在 CI log 逐條可見 -------------------------------

def _step(msg: str) -> None:
    print(f"    ▶ {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"    ✅ {msg}", flush=True)


def _show(event: dict) -> None:
    print(
        "       ↳ Core Event: "
        f"event_id={event['event_id']} "
        f"type={event['event_type']} lifecycle={event['lifecycle']} "
        f"technique={event['technique']} visibility={event['visibility']} "
        f"scenario={event['scenario_id']} observed_at={event['observed_at']}",
        flush=True,
    )


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
        pass


def test_normal_login_does_not_trigger_sqli_detection(events):
    """負向：正常登入**不該**產生 SQLi 偵測事件（證明規則非恆真）。"""
    print("\n=== [負向] 正常登入不觸發 SQLi 偵測 ===", flush=True)
    mark = events.now()
    _step("送 3 次正常登入（少量，不跨 brute force 門檻）")
    for _ in range(3):
        _normal_login()

    _step("等 25s，確認這段時間內沒有 sqli-01 事件冒出來")
    with pytest.raises(EventNotSeen):
        wait_for_event(
            fetch=lambda: events.since(mark),
            match=lambda e: e["scenario_id"] == "sqli-01",
            what="不該出現的 sqli-01 事件",
            timeout_s=25,
            poll_s=2.0,
        )
    _ok("25s 內沒有 sqli-01 事件 —— 規則不是永遠開火")


def test_real_sqli_becomes_a_core_event(events):
    """happy path：真 SQLi 走完 log 管線 → attack.detected Core Event。"""
    print("\n=== [happy path] 真 SQLi → Core Event（log 路徑）===", flush=True)
    mark = events.now()
    _step("注入 5 次 SQLi 到 /login")
    for _ in range(5):
        result = inject_sqli(APP_URL, path="/login", param="username")
        assert result.status_code in (200, 401)

    _step("等管線：app log → Alloy → Loki → Grafana(eval 10s) → webhook → receiver")
    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01" and e["event_type"] == "attack.detected",
        what="真實 SQLi 走完管線後的 attack.detected Core Event",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    _show(core)
    assert_core_event(core)
    _ok("符合 Core Event 契約")
    assert core["technique"] == "T1190"
    _ok("technique = T1190")
    assert core["visibility"] == "public"
    _ok("visibility = public（rule 無法覆寫）")
    assert "evidence_ref" not in core and "loki" not in repr(core).lower()
    _ok("無 evidence_ref、全文無 loki 字樣")


def test_bruteforce_metric_becomes_a_core_event(events):
    """metric 路徑：大量失敗登入 → Prometheus → Grafana PromQL 告警 → Core Event。"""
    print("\n=== [metric 路徑] brute force → Prometheus → Core Event ===", flush=True)
    mark = events.now()
    _step("送 20 次失敗登入（跨過 brute force 門檻 > 10/1m）")
    for _ in range(20):
        _normal_login("attacker")

    _step("等管線：/metrics → Prometheus:9090 → Grafana PromQL → webhook → receiver")
    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "bruteforce-01" and e["event_type"] == "attack.detected",
        what="brute force metric 走完後的 attack.detected Core Event",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    _show(core)
    assert_core_event(core)
    assert core["technique"] == "T1110"
    _ok("technique = T1110（走 metric 路徑，非 log）")


def test_sqli_alert_resolves_after_attack_stops(events):
    """lifecycle：攻擊停止後 Grafana 送 resolved，與 firing 共用同一 event_id。"""
    print("\n=== [lifecycle] 攻擊停止 → resolved（與 firing 共用 event_id）===", flush=True)
    mark = events.now()
    _step("注入 5 次 SQLi，等 firing")
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
    _show(firing)
    _ok(f"firing 取得，event_id={firing_id}")

    _step("停止攻擊，等 SQLi 從 1 分鐘窗老化，Grafana 才會 resolve（可能 >60s）")
    resolved = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["event_id"] == firing_id and e["lifecycle"] == "resolved",
        what="resolved（與 firing 共用 event_id）",
        timeout_s=RESOLVE_TIMEOUT_S,
        poll_s=3.0,
    )
    _show(resolved)
    assert resolved["event_id"] == firing_id
    _ok(f"resolved 與 firing 共用 event_id={firing_id}")
