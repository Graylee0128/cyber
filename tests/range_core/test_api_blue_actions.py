"""`POST /api/blue-actions` and its effect on `GET /api/score` (#36 Phase 2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from purple.store.events import CoreEventStore

from range_core.api import create_app
from range_core.scenarios import Scenario, ScenarioCatalog

TOKEN_MAP = {
    "red-secret": "red",
    "blue-secret": "blue",
    "instructor-secret": "instructor",
}
BLUE_AUTH = {"Authorization": "Bearer blue-secret"}
RED_AUTH = {"Authorization": "Bearer red-secret"}
INSTRUCTOR_AUTH = {"Authorization": "Bearer instructor-secret"}
ACTION_AT = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return ACTION_AT


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "blue-api-01",
            "name": "Blue API",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [{"id": "capture_flag", "evaluation": "submission", "points": 500}],
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "attack_chain": [
                {"id": "exploit-web", "technique": "T1190", "description": "Exploit."}
            ],
            "reset_scope": "exercise",
        }
    )


class _FakeDispatcher:
    """#51 測試接縫：不真的打 HTTP，直接回傳設定好的結果。"""

    def __init__(self, outcome: bool):
        self.outcome = outcome

    def dispatch(self, exercise_id: str, event_id: str) -> bool:
        return self.outcome


def make_app(exercise_store, pg_connection, *, dispatch_outcome: bool | None = None):
    """`dispatch_outcome=None`（預設）沿用 production 的 `HttpResponseDispatcher`
    ——沒設定 URL／token 時它自己就會回 False，不用另外注入假的。要驗證
    「派送成功會怎樣」的測試才需要傳 `True`。"""
    return create_app(
        ScenarioCatalog((scenario(),)),
        exercise_store=exercise_store,
        conn=pg_connection,
        token_map=TOKEN_MAP,
        action_clock=FixedClock(),
        response_dispatcher_factory=(
            None if dispatch_outcome is None else lambda conn: _FakeDispatcher(dispatch_outcome)
        ),
    )


def start(app) -> dict:
    resp = TestClient(app, headers=INSTRUCTOR_AUTH).post(
        "/api/exercises/start",
        json={
            "scenario_id": "blue-api-01",
            "players": [{"player_id": "red-alice", "source_ip": "10.167.30.11"}],
        },
    )
    assert resp.status_code == 201
    return resp.json()


def write_event(conn, exercise_id: str, event_id: str, *, technique: str = "T1190") -> None:
    CoreEventStore(conn).append(
        {
            "event_id": event_id,
            "exercise_id": exercise_id,
            "scenario_id": "blue-api-01",
            "event_type": "attack.detected",
            "lifecycle": "firing",
            "severity": "high",
            "source": "grafana",
            "team": "red",
            "technique": technique,
            "target": {"service": "range-target"},
            "observed_at": (ACTION_AT - timedelta(seconds=30)).isoformat(),
            "visibility": "red",
            "action_id": None,
        }
    )


class TestClearanceGate:
    def test_red_cannot_submit_blue_actions(self, exercise_store, pg_connection):
        """WS3 §5.1：「誰」恆為 blue，clearance 掛在 Console 服務上 —— 這是
        唯一擋住紅隊自己提交藍隊動作的機制。"""
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")

        resp = TestClient(app, headers=RED_AUTH).post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "acknowledge"}
        )

        assert resp.status_code == 403

    def test_identity_cannot_be_self_reported_by_a_header(self, exercise_store, pg_connection):
        """#51 AC：「Console 的身分是服務級，一條測試證明呼叫者無法自報
        clearance（沿用 CALLER_CLEARANCE 的既有保證）」。

        紅隊 token 換出的身分是 `red`；即使請求帶著看起來像藍隊自報的
        標頭（沿用舊系統曾經存在過的 `X-Purple-Identity` 這類欄位名），
        `require_identity` 只認 `Authorization: Bearer <token>` 換出來的
        結果（`disclosure.identity.extract_token` 只讀這一個來源），
        其餘標頭一律被忽略——身分不是「呼叫者說了算」，是「token 換得出
        什麼就是什麼」。"""
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")

        resp = TestClient(app, headers={**RED_AUTH, "X-Purple-Identity": "blue"}).post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "acknowledge"}
        )

        assert resp.status_code == 403  # 紅隊 token 換出的身分還是 red，自報的標頭沒用

    def test_blue_can_submit(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")

        resp = TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "acknowledge"}
        )

        assert resp.status_code == 201
        assert resp.json() == {
            "team": "blue",
            "action": "acknowledge",
            "event_id": "evt-1",
            "submitted_at": ACTION_AT.isoformat(),
            # #51: only `contain` ever dispatches; every other action's
            # status is always None, not an omitted key.
            "dispatch_status": None,
        }

    @pytest.mark.parametrize(
        ("action", "extra"),
        [
            ("acknowledge", {}),
            ("classify", {"technique": "T1190"}),
            ("contain", {}),
            ("resolve", {}),
            ("dismiss", {}),
        ],
    )
    def test_all_five_contract_actions_enter_event_service(
        self, exercise_store, pg_connection, action, extra
    ):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        event_id = f"evt-{action}"
        write_event(pg_connection, started["exercise_id"], event_id)

        resp = TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions",
            json={"event_id": event_id, "action": action, **extra},
        )

        assert resp.status_code == 201
        assert resp.json()["action"] == action


class TestValidation:
    def test_caller_cannot_supply_identity_or_arrival_time(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")

        resp = TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions",
            json={
                "event_id": "evt-1",
                "action": "acknowledge",
                "team": "instructor",
                "submitted_at": "2000-01-01T00:00:00Z",
            },
        )

        assert resp.status_code == 422

    def test_unknown_action_is_400(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")

        resp = TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "escalate"}
        )

        assert resp.status_code == 400

    def test_unknown_event_is_404(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions", json={"event_id": "evt-never-happened", "action": "acknowledge"}
        )

        assert resp.status_code == 404

    def test_second_classify_is_409(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")
        client = TestClient(app, headers=BLUE_AUTH)
        client.post(
            "/api/blue-actions",
            json={"event_id": "evt-1", "action": "classify", "technique": "T1190"},
        )

        resp = client.post(
            "/api/blue-actions",
            json={"event_id": "evt-1", "action": "classify", "technique": "T1110"},
        )

        assert resp.status_code == 409

    def test_no_running_exercise_is_404(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)

        resp = TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "acknowledge"}
        )

        assert resp.status_code == 404


class TestScoreIntegration:
    def test_correct_classify_awards_identify_technique(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1", technique="T1190")
        client = TestClient(app, headers=BLUE_AUTH)

        client.post(
            "/api/blue-actions",
            json={"event_id": "evt-1", "action": "classify", "technique": "T1190"},
        )

        blue = client.get("/api/score").json()["blue"]
        assert blue["total"] == 100
        assert blue["events"][0]["judgement"] == "correct"

    def test_wrong_classify_awards_nothing(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1", technique="T1190")
        client = TestClient(app, headers=BLUE_AUTH)

        client.post(
            "/api/blue-actions",
            json={"event_id": "evt-1", "action": "classify", "technique": "T1110"},
        )

        blue = client.get("/api/score").json()["blue"]
        assert blue["total"] == 0
        assert blue["events"][0]["judgement"] == "wrong"

    def test_acknowledge_and_contain_stack(self, exercise_store, pg_connection):
        """contain 只有在真的派送成功時才計分（#51）——這裡注入一個永遠
        成功的假 dispatcher，測的是分數疊加，不是派送本身。"""
        app = make_app(exercise_store, pg_connection, dispatch_outcome=True)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")
        client = TestClient(app, headers=BLUE_AUTH)

        client.post("/api/blue-actions", json={"event_id": "evt-1", "action": "acknowledge"})
        contain_resp = client.post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "contain"}
        )

        assert contain_resp.json()["dispatch_status"] == "dispatched"
        blue = client.get("/api/score").json()["blue"]
        assert blue["total"] == 100 + 150  # detect_attack + contain

    def test_contain_without_a_reachable_z_mgmt_does_not_score(
        self, exercise_store, pg_connection
    ):
        """#51 的核心正確性：派送失敗時，contain 不得出現「有分數沒封鎖」。
        `make_app` 預設沒配 Z-MGMT URL/token，`HttpResponseDispatcher` 自然
        失敗——這條刻意不注入假 dispatcher，驗證的正是這個 production 預設
        路徑。"""
        app = make_app(exercise_store, pg_connection)  # 沒有 dispatch_outcome
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")
        client = TestClient(app, headers=BLUE_AUTH)

        contain_resp = client.post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "contain"}
        )

        assert contain_resp.status_code == 201
        assert contain_resp.json()["dispatch_status"] == "failed"
        blue = client.get("/api/score").json()["blue"]
        assert blue["total"] == 0  # 落地了，但沒有分數——不是「有分數沒封鎖」
        assert blue["events"][0]["awarded"] == 0

    def test_dispatch_failure_still_lands_the_action(self, exercise_store, pg_connection):
        """派送失敗不影響落地——AC「Blue Action 先落地、再派送」的落地
        那一半必須先於派送結果存在，兩者不是同一個原子操作的一部分。"""
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-1")
        client = TestClient(app, headers=BLUE_AUTH)

        resp = client.post(
            "/api/blue-actions", json={"event_id": "evt-1", "action": "contain"}
        )

        assert resp.status_code == 201  # 落地成功，即使派送之後才發現失敗
        assert resp.json()["event_id"] == "evt-1"

    def test_events_blue_never_touched_do_not_appear(self, exercise_store, pg_connection):
        """derive_blue_scores 只列藍隊有動作的事件 —— 不然分數會隨著跟藍隊
        無關的事件量變動。"""
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-untouched")
        client = TestClient(app, headers=BLUE_AUTH)

        blue = client.get("/api/score").json()["blue"]

        assert blue == {"total": 0, "events": []}

    def test_dismiss_without_execution_evidence_is_scored_not_a_500(
        self, exercise_store, pg_connection
    ):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        write_event(pg_connection, started["exercise_id"], "evt-false-positive")
        client = TestClient(app, headers=BLUE_AUTH)

        submitted = client.post(
            "/api/blue-actions",
            json={"event_id": "evt-false-positive", "action": "dismiss"},
        )
        scored = client.get("/api/score")

        assert submitted.status_code == 201
        assert scored.status_code == 200
        assert scored.json()["blue"]["events"][0]["judgement"] == "dismissed_correctly"
