"""端到端部署測試 —— 需要 docker compose 全棧（`-m integration`）。

契約測試（tests/pipeline）是手餵 webhook；這裡打真的請求，讓它自己走完：
    vulnerable-app → Alloy → Loki / Prometheus → Grafana（唯一 alert engine）
    → webhook → P1 receiver → PostgreSQL

測試在檔案內的**順序有意義**：負向先跑（此時 Loki 還沒有任何 SQLi 記錄），
resolved 的慢測試放最後。凡是會觸發 sqli-01 規則的測試，結束前都要等自己那個
event_id 的 alert 真的 resolved 才收尾——否則會把還在 firing 的狀態留給下一條
測試，讓它以為自己乾淨開局，實際上是在別人污染過的 60s 偵測視窗裡起跑（真環境
下實測過：沒清乾淨會讓 test_sqli_alert_resolves_after_attack_stops 在同一次
full-suite 執行裡失敗，單獨跑卻是乾淨的）。

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
ENGINE_URL = os.environ.get("PURPLE_ENGINE_URL", "http://localhost:8001")

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

    _step("等這次攻擊的 alert 自然 resolve，避免留下 firing 狀態污染後續測試的 60s 偵測視窗")
    wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["event_id"] == core["event_id"] and e["lifecycle"] == "resolved",
        what="本測試自己的 sqli-01 alert resolved（測試隔離用）",
        timeout_s=RESOLVE_TIMEOUT_S,
        poll_s=3.0,
    )
    _ok("alert 已 resolve，偵測視窗乾淨")


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


def test_evidence_api_returns_context_from_real_loki(events):
    """Evaluation Engine v0：SQLi → Core Event → 呼叫 Evidence API 取回上下文窗。

    證明 Z-MGMT 唯一缺席的軟體住戶到位，且 resolver 真的對真 Loki 取證
    （非 fake）。以 blue 身分呼叫，看得到 raw 遙測行。"""
    import json
    import urllib.request

    print("\n=== [Evidence API] SQLi → Core Event → 取回上下文窗（真 Loki）===", flush=True)
    mark = events.now()
    _step("注入 5 次 SQLi")
    for _ in range(5):
        inject_sqli(APP_URL, path="/login", param="username")

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01" and e["lifecycle"] == "firing",
        what="firing（供 Evidence API 查詢）",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    event_id = core["event_id"]
    _ok(f"取得 event_id={event_id}")

    _step(f"以 blue 身分呼叫 GET /evidence/{event_id}")
    req = urllib.request.Request(
        f"{ENGINE_URL}/evidence/{event_id}", headers={"X-Purple-Identity": "blue"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200
        body = json.loads(resp.read())

    print(
        f"       ↳ Evidence: rule={body['rule']} line_count={body['line_count']} "
        f"window={body['window_start']}..{body['window_end']}",
        flush=True,
    )
    assert body["event_id"] == event_id
    assert body["line_count"] >= 1, "上下文窗應至少有一行（真 Loki 取回）"
    assert "backend" not in json.dumps(body).lower()
    _ok(f"取回 {body['line_count']} 行上下文，無 backend 洩漏")

    _step("等這次攻擊的 alert 自然 resolve，避免留下 firing 狀態污染後面的 lifecycle 測試")
    wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["event_id"] == event_id and e["lifecycle"] == "resolved",
        what="本測試自己的 sqli-01 alert resolved（測試隔離用；緊接在後的 "
        "test_sqli_alert_resolves_after_attack_stops 需要乾淨的 60s 偵測視窗）",
        timeout_s=RESOLVE_TIMEOUT_S,
        poll_s=3.0,
    )
    _ok("alert 已 resolve，偵測視窗乾淨")


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
