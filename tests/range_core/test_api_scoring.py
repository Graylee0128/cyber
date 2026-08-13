"""#33: /api/submissions, /api/hints, /api/objectives/sync, /api/score over HTTP."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from purple.store.events import CoreEventStore

from range_core.api import create_app
from range_core.flags import FixtureFlagSource, SharedFileFlagSource
from range_core.scenarios import Scenario, ScenarioCatalog

ALICE = ("10.167.30.11", 12345)
BOB = ("10.167.30.12", 12345)
OUTSIDER = ("10.167.30.13", 12345)
FLAG = "flag{" + "a" * 32 + "}"

# #52 B2: every range_core endpoint now also requires a service token proving
# the caller is a legitimate range participant, on top of #33's per-player
# source-IP roster attribution. One shared team token is enough here — which
# *player* within the team is still resolved by source IP.
#
# #49: starting/resetting an exercise now needs instructor clearance
# (`ENDPOINT_MIN_CLEARANCE`), so the fixture that sets up a running exercise
# carries the instructor token. Gameplay calls stay on the red token — which is
# the point: a red player must not be able to reset the exercise they are losing.
TOKEN_MAP = {
    "red-secret": "red",
    "blue-secret": "blue",
    "instructor-secret": "instructor",
}
AUTH = {"Authorization": "Bearer red-secret"}
BLUE_AUTH = {"Authorization": "Bearer blue-secret"}
INSTRUCTOR_AUTH = {"Authorization": "Bearer instructor-secret"}
ACTION_AT = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


class FixedClock:
    def now(self) -> datetime:
        return ACTION_AT


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "range-chain-01",
            "name": "Range Chain",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {
                    "id": "gain_shell",
                    "evaluation": "telemetry",
                    "points": 200,
                    "telemetry_signal": {"action_id": "gain-shell"},
                },
                {"id": "capture_flag", "evaluation": "submission", "points": 500},
            ],
            "hints": [
                {"objective_id": "capture_flag", "text": "check /var/lib", "penalty_percent": 50}
            ],
            "targets": [{"host": "target-01", "surfaces": ["web", "local-shell"]}],
            "expected_sources": ["falco", "alloy"],
            "attack_chain": [
                {"id": "gain-shell", "technique": "T1059", "description": "Gain a shell."}
            ],
            "reset_scope": "environment",
        }
    )


class _FakeDispatcher:
    """#51 測試接縫：不真的打 HTTP，直接回傳設定好的結果。"""

    def __init__(self, outcome: bool):
        self.outcome = outcome

    def dispatch(self, exercise_id: str, event_id: str) -> bool:
        return self.outcome


def make_app(exercise_store, pg_connection, flag_source=None, dispatch_outcome=None):
    return create_app(
        ScenarioCatalog((scenario(),)),
        exercise_store=exercise_store,
        conn=pg_connection,
        flag_source=flag_source or FixtureFlagSource(FLAG),
        token_map=TOKEN_MAP,
        action_clock=FixedClock(),
        response_dispatcher_factory=(
            None if dispatch_outcome is None else lambda conn: _FakeDispatcher(dispatch_outcome)
        ),
    )


def as_actor(app, source: tuple[str, int]) -> TestClient:
    """`starlette.testclient` fixes `request.client` at construction, not
    per-request (matches production: one TCP connection = one peer
    address). Different actors against the same running exercise are
    different `TestClient`s wrapping the same `app`/connection. The shared
    `AUTH` header proves team membership (#52 B2); `source` still resolves
    which player within the team, via the roster."""
    return TestClient(app, client=source, headers=AUTH)


def start(app) -> dict:
    resp = TestClient(app, client=("0.0.0.0", 0), headers=INSTRUCTOR_AUTH).post(
        "/api/exercises/start",
        json={
            "scenario_id": "range-chain-01",
            "players": [
                {"player_id": "red-alice", "source_ip": "10.167.30.11"},
                {"player_id": "red-bob", "source_ip": "10.167.30.12"},
            ],
        },
    )
    assert resp.status_code == 201
    return resp.json()


class TestSubmissions:
    def test_correct_flag_completes_the_objective(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).post(
            "/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG}
        )

        assert resp.status_code == 200
        assert resp.json() == {
            "accepted": True,
            "objective_id": "capture_flag",
            "player_id": "red-alice",
        }

    def test_wrong_flag_is_a_normal_200_not_an_error(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).post(
            "/api/submissions",
            json={"objective_id": "capture_flag", "flag": "flag{" + "0" * 32 + "}"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"accepted": False}

    def test_telemetry_objective_rejects_submission(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).post(
            "/api/submissions", json={"objective_id": "gain_shell", "flag": FLAG}
        )

        assert resp.status_code == 409

    def test_non_roster_source_ip_is_rejected(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, OUTSIDER).post(
            "/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG}
        )

        assert resp.status_code == 403

    def test_missing_flag_file_returns_503_and_writes_nothing(
        self, exercise_store, pg_connection, tmp_path
    ):
        app = make_app(
            exercise_store, pg_connection, flag_source=SharedFileFlagSource(tmp_path / "missing.txt")
        )
        start(app)

        resp = as_actor(app, ALICE).post(
            "/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG}
        )

        assert resp.status_code == 503
        rows = pg_connection.execute(
            "SELECT count(*) FROM exercise_objective_completions"
        ).fetchone()
        assert rows[0] == 0

    def test_non_ascii_submission_is_rejected_not_500(self, exercise_store, pg_connection):
        """#33 review finding: `hmac.compare_digest` raises on non-ASCII
        `str` input. A player pasting full-width characters or smart quotes
        must get a normal `{"accepted": false}`, not a 500."""
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).post(
            "/api/submissions", json={"objective_id": "capture_flag", "flag": "flag{全形字元}"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"accepted": False}

    def test_repeat_correct_submission_stays_idempotent(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)
        alice = as_actor(app, ALICE)

        alice.post("/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG})
        alice.post("/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG})

        rows = pg_connection.execute(
            "SELECT count(*) FROM exercise_objective_completions"
        ).fetchone()
        assert rows[0] == 1


class TestHints:
    def test_get_hints_never_returns_text(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).get("/api/hints", params={"objective_id": "capture_flag"})

        assert resp.status_code == 200
        assert resp.json() == [{"index": 0, "penalty_percent": 50}]
        assert "check /var/lib" not in resp.text

    def test_post_hints_returns_text_and_records_usage(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).post(
            "/api/hints", json={"objective_id": "capture_flag", "hint_index": 0}
        )

        assert resp.status_code == 200
        assert resp.json()["text"] == "check /var/lib"
        rows = pg_connection.execute("SELECT count(*) FROM exercise_hint_usages").fetchone()
        assert rows[0] == 1


class TestScore:
    def test_score_reflects_submission_and_hint_usage(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)
        alice = as_actor(app, ALICE)
        alice.post("/api/hints", json={"objective_id": "capture_flag", "hint_index": 0})
        alice.post("/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG})

        resp = as_actor(app, ALICE).get("/api/score")

        assert resp.status_code == 200
        players = resp.json()["red"]["players"]
        red_alice = next(p for p in players if p["player_id"] == "red-alice")
        assert red_alice["total"] == 250  # 500 * (1 - 0.5)

    def test_score_includes_telemetry_completions_via_sync(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        event = {
            "event_id": "evt-1",
            "exercise_id": started["exercise_id"],
            "scenario_id": "range-chain-01",
            "event_type": "attack.detected",
            "lifecycle": "firing",
            "severity": "high",
            "source": "grafana",
            "team": "red",
            "technique": "T1059",
            "target": {"service": "range-target", "source_ip": "10.167.30.11"},
            "observed_at": "2026-08-13T06:00:00+00:00",
            "visibility": "red",
            "action_id": "gain-shell",
        }
        CoreEventStore(pg_connection).append(event)

        resp = as_actor(app, ALICE).get("/api/score")

        players = resp.json()["red"]["players"]
        red_alice = next(p for p in players if p["player_id"] == "red-alice")
        assert red_alice["total"] == 200

    def test_no_running_exercise_is_404(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)

        assert as_actor(app, ALICE).get("/api/score").status_code == 404

    def test_score_now_has_a_blue_key(self, exercise_store, pg_connection):
        """ADR 0002 決定②：v1 沒有 blue key 是因為當時沒有 Blue Action 契約，
        「待 WS3 交付後加 blue key 是加法」——#36 Phase 2 就是那個交付，這條
        測試取代原本的 `test_score_has_no_blue_key`。沒有任何 Blue Action 時
        blue.total 是 0，不是缺欄位（WS1 §1.1 的非零和現在是真的可測，不再
        空真成立）。"""
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).get("/api/score")

        assert resp.json()["blue"] == {"total": 0, "events": []}

    def test_blue_score_does_not_affect_red_score(self, exercise_store, pg_connection):
        """WS1 §1.1 非零和：現在有真的 Blue Action 可以造出非零分數，斷言
        它不影響 red 的分數。"""
        app = make_app(exercise_store, pg_connection)
        started = start(app)
        exercise_id = started["exercise_id"]
        CoreEventStore(pg_connection).append(
            {
                "event_id": "evt-blue-1",
                "exercise_id": exercise_id,
                "scenario_id": "range-chain-01",
                "event_type": "attack.detected",
                "lifecycle": "firing",
                "severity": "high",
                "source": "grafana",
                "team": "red",
                "technique": "T1059",
                "target": {"service": "range-target"},
                "observed_at": (ACTION_AT - timedelta(seconds=30)).isoformat(),
                "visibility": "red",
                "action_id": None,
            }
        )
        red_before = as_actor(app, ALICE).get("/api/score").json()["red"]

        TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions", json={"event_id": "evt-blue-1", "action": "acknowledge"}
        )

        resp = as_actor(app, ALICE).get("/api/score")
        assert resp.json()["red"] == red_before
        assert resp.json()["blue"]["total"] == 100  # detect_attack

    def test_score_is_unaffected_by_p2_purple_data(self, exercise_store, pg_connection):
        """Both score paths ignore P2's MTTR/containment/coverage outputs.

        Keep a real non-zero Blue score in the response, then seed P2's
        registry, event, and latency summary. The entire score response must
        remain byte-identical; only Core Event -> Blue Action arrival time is
        a Blue scoring input.
        """
        from purple.evaluation.action_registry import ActionRegistryStore, RegisteredAction
        from purple.receiver.whitelist import default_whitelist

        app = make_app(exercise_store, pg_connection, dispatch_outcome=True)
        started = start(app)
        alice = as_actor(app, ALICE)
        alice.post("/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG})

        CoreEventStore(pg_connection).append(
            {
                "event_id": "evt-blue-score",
                "exercise_id": started["exercise_id"],
                "scenario_id": "range-chain-01",
                "event_type": "attack.detected",
                "lifecycle": "firing",
                "severity": "high",
                "source": "grafana",
                "team": "red",
                "technique": "T1059",
                "target": {"service": "range-target"},
                "observed_at": (ACTION_AT - timedelta(seconds=30)).isoformat(),
                "visibility": "red",
                "action_id": None,
            }
        )
        TestClient(app, headers=BLUE_AUTH).post(
            "/api/blue-actions",
            json={"event_id": "evt-blue-score", "action": "contain"},
        )

        before = as_actor(app, ALICE).get("/api/score").json()
        assert before["blue"]["total"] == 150

        registry = ActionRegistryStore(pg_connection, default_whitelist())
        registry.seed(
            started["exercise_id"],
            "range-chain-01",
            [RegisteredAction("a-1", "T1190", "unrelated P2 action")],
        )
        registry.freeze(started["exercise_id"])
        CoreEventStore(pg_connection).append(
            {
                "event_id": "evt-p2",
                "exercise_id": started["exercise_id"],
                "scenario_id": "range-chain-01",
                "event_type": "attack.detected",
                "lifecycle": "firing",
                "severity": "high",
                "source": "grafana",
                "team": "red",
                "technique": "T1190",
                "target": {"service": "range-target"},
                "observed_at": "2026-08-13T06:00:00+00:00",
                "visibility": "red",
                "action_id": "a-1",
            }
        )
        pg_connection.execute(
            """
            INSERT INTO latency_summaries
                (exercise_id, mode, sample_count, mttd_p50_ms, mttd_p95_ms,
                 mttr_p50_ms, mttr_p95_ms, containment_p50_ms, containment_p95_ms)
            VALUES (%s, 'exercise', 20, 1000, 2000, 3000, 4000, 5000, 6000)
            """,
            (started["exercise_id"],),
        )

        after = as_actor(app, ALICE).get("/api/score").json()

        assert after == before
