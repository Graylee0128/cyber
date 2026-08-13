"""#33: /api/submissions, /api/hints, /api/objectives/sync, /api/score over HTTP."""

from __future__ import annotations

from fastapi.testclient import TestClient
from purple.store.events import CoreEventStore

from range_core.api import create_app
from range_core.flags import FixtureFlagSource, SharedFileFlagSource
from range_core.scenarios import Scenario, ScenarioCatalog

ALICE = ("10.167.30.11", 12345)
BOB = ("10.167.30.12", 12345)
OUTSIDER = ("10.167.30.13", 12345)
FLAG = "flag{" + "a" * 32 + "}"


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


def make_app(exercise_store, pg_connection, flag_source=None):
    return create_app(
        ScenarioCatalog((scenario(),)),
        exercise_store=exercise_store,
        conn=pg_connection,
        flag_source=flag_source or FixtureFlagSource(FLAG),
    )


def as_actor(app, source: tuple[str, int]) -> TestClient:
    """`starlette.testclient` fixes `request.client` at construction, not
    per-request (matches production: one TCP connection = one peer
    address). Different actors against the same running exercise are
    different `TestClient`s wrapping the same `app`/connection."""
    return TestClient(app, client=source)


def start(app) -> dict:
    resp = as_actor(app, ("0.0.0.0", 0)).post(
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

    def test_score_has_no_blue_key(self, exercise_store, pg_connection):
        app = make_app(exercise_store, pg_connection)
        start(app)

        resp = as_actor(app, ALICE).get("/api/score")

        assert "blue" not in resp.json()

    def test_score_is_unaffected_by_p2_purple_data(self, exercise_store, pg_connection):
        """`derive_scores` takes no I/O — it cannot reach `purple.metrics`.
        Behavioral corroboration of that structural guarantee: seed a
        frozen Action Registry and Core Events (P2's inputs), score before
        and after, and require byte-identical output."""
        from purple.evaluation.action_registry import ActionRegistryStore, RegisteredAction
        from purple.receiver.whitelist import default_whitelist

        app = make_app(exercise_store, pg_connection)
        started = start(app)
        alice = as_actor(app, ALICE)
        alice.post("/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG})

        before = as_actor(app, ALICE).get("/api/score").json()

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

        after = as_actor(app, ALICE).get("/api/score").json()

        assert after == before
