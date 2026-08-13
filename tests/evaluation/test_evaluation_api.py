"""#90 Phase 3：`GET /api/exercises/{id}/evaluation` —— 四個核心數字同進同出。

T2（需要 PG）：走真的 Action Registry、真的 Core Event 儲存、真的執行紀錄。
只有遙測後端與 Source Registry 是可注入的 fake —— 前者是外部服務，後者需要
Loki heartbeat，兩者都不屬於本票要驗的判定邏輯。
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from purple.evaluation.api import create_app
from purple.evidence.backends import ContextLine, FakeBackend
from purple.receiver.core import build_alert_record, build_core_event
from purple.registry.source_registry import ScenarioSource, evaluate_registry
from purple.store.alerts import AlertRecordStore
from purple.store.db import connect
from purple.store.events import CoreEventStore
from purple.store.executions import ActionExecutionStore
from range_core.scenarios import Scenario, ScenarioCatalog

NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
EXECUTED = NOW - timedelta(seconds=60)
MARKER = "-- purple:deadbeef action:a-1"
EXERCISE = "ex-eval"

CATALOG = ScenarioCatalog(
    scenarios=(
        Scenario.model_validate(
            {
                "id": "sqli-01",
                "name": "SQLi",
                "difficulty": "intro",
                "duration": "30m",
                "objectives": [{"id": "o-1", "evaluation": "telemetry", "points": 100}],
                "targets": [{"host": "target", "surfaces": ["http"]}],
                "expected_sources": ["alloy"],
                "attack_chain": [
                    {"id": "a-1", "technique": "T1190", "description": "exploit app"}
                ],
                "reset_scope": "exercise",
            }
        ),
    )
)


def source_registry(scenario_id):
    return evaluate_registry(
        scenario_id, [ScenarioSource("alloy")], {"alloy": NOW}, NOW
    )


def telemetry(*lines):
    return FakeBackend(lines=lines)


def marker_line(marker=MARKER, source="alloy"):
    return ContextLine(
        timestamp=EXECUTED + timedelta(seconds=1),
        line=f"GET /login?u=' OR '1'='1 {marker}",
        source=source,
    )


def client(backend):
    return TestClient(
        create_app(connect, CATALOG, source_registry=source_registry, telemetry=backend)
    )


def alert(action_id, *, technique="T1190"):
    return {
        "status": "firing",
        "startsAt": EXECUTED.isoformat(),
        "labels": {
            "event_type": "attack.detected",
            "technique": technique,
            "service": "vulnerable-app",
            "severity": "high",
            "team": "red",
            "scenario_id": "sqli-01",
            "alertname": "SQLi detected",
            "source": "alloy",
            **({"action_id": action_id} if action_id else {}),
        },
    }


def seed_detection(conn, event_id, action_id):
    raw = alert(action_id)
    AlertRecordStore(conn).write(build_alert_record(raw, event_id))
    CoreEventStore(conn).append(
        build_core_event(raw, event_id, "firing", exercise_id=EXERCISE)
    )


def register_and_freeze(api):
    assert api.post(
        f"/api/exercises/{EXERCISE}/actions", json={"scenario_id": "sqli-01"}
    ).status_code == 201
    assert api.post(f"/api/exercises/{EXERCISE}/actions/freeze").status_code == 200


def test_unfrozen_registry_is_a_conflict_not_an_empty_report(pg_connection):
    api = client(telemetry())
    assert api.post(
        f"/api/exercises/{EXERCISE}/actions", json={"scenario_id": "sqli-01"}
    ).status_code == 201

    response = api.get(f"/api/exercises/{EXERCISE}/evaluation")
    # 未凍結不是「還沒有資料」，是「這個分母還會變」。200＋空集合會被讀成 0%。
    assert response.status_code == 409
    assert "frozen" in response.json()["detail"]


def test_detected_action_is_scored_with_all_four_numbers(pg_connection):
    api = client(telemetry(marker_line()))
    register_and_freeze(api)
    ActionExecutionStore(pg_connection).record(EXERCISE, "a-1", EXECUTED, MARKER)
    seed_detection(pg_connection, "evt-hit", "a-1")

    body = api.get(f"/api/exercises/{EXERCISE}/evaluation").json()

    assert set(body["metrics"]) == {
        "action_coverage",
        "confirmation_rate",
        "alert_volume",
        "excluded_counts",
    }
    assert body["metrics"]["action_coverage"] == 1.0
    assert body["metrics"]["alert_volume"] == 1
    assert body["metrics"]["excluded_counts"] == {"unknown": 0, "not_executed": 0}
    assert body["actions"] == [
        {
            "action_id": "a-1",
            "state": "hit",
            # 單一來源：C2。升到 C3 需要第二個獨立 collector。
            "level": "C2",
            "gap": None,
            "reason": body["actions"][0]["reason"],
            "event_ids": ["evt-hit"],
        }
    ]


def test_detection_without_action_id_does_not_count_as_coverage(pg_connection):
    api = client(telemetry(marker_line()))
    register_and_freeze(api)
    ActionExecutionStore(pg_connection).record(EXERCISE, "a-1", EXECUTED, MARKER)
    # 有偵測、時間也對得上，但沒有帶關聯鍵 —— 不得被算到 a-1 頭上。
    seed_detection(pg_connection, "evt-orphan", None)

    body = api.get(f"/api/exercises/{EXERCISE}/evaluation").json()
    assert body["actions"][0]["state"] == "miss"
    assert body["actions"][0]["gap"] == "detection_gap"
    assert body["metrics"]["action_coverage"] == 0.0


def test_empty_denominator_reports_null_not_zero_percent(pg_connection):
    api = client(telemetry())
    register_and_freeze(api)
    # 沒有任何執行紀錄 → 全部 not_executed → 分母是 0。

    body = api.get(f"/api/exercises/{EXERCISE}/evaluation").json()
    assert body["metrics"]["action_coverage"] is None
    assert body["metrics"]["confirmation_rate"] is None
    assert body["metrics"]["excluded_counts"] == {"unknown": 0, "not_executed": 1}
    assert body["actions"][0]["state"] == "not_executed"


def test_response_never_uses_the_banned_detection_rate_label(pg_connection):
    api = client(telemetry(marker_line()))
    register_and_freeze(api)
    ActionExecutionStore(pg_connection).record(EXERCISE, "a-1", EXECUTED, MARKER)
    seed_detection(pg_connection, "evt-hit", "a-1")

    assert "detection_rate" not in api.get(
        f"/api/exercises/{EXERCISE}/evaluation"
    ).text


# ── #90 Phase 4：latency 持久化 endpoint ───────────────────────────────────


def test_latency_reads_back_empty_before_anything_is_computed(pg_connection):
    api = client(telemetry())
    body = api.get(f"/api/exercises/{EXERCISE}/latency").json()
    # 「還沒算過」是合法狀態，200＋空集合而非 404。
    assert body == {"modes": {}}


def test_latency_compute_rejects_fewer_than_twenty_runs(pg_connection):
    api = client(telemetry())
    register_and_freeze(api)
    ActionExecutionStore(pg_connection).record(EXERCISE, "a-1", EXECUTED, MARKER)
    # 只有 1 筆執行 → 遠少於 20 → 不得當最終量測交付。
    response = api.post(f"/api/exercises/{EXERCISE}/latency")
    assert response.status_code == 409
    assert "20" in response.json()["detail"]
