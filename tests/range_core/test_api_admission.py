"""Admission-to-Range-Core exercise/player lifecycle through the HTTP seam."""

from __future__ import annotations

from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.flags import FixtureFlagSource
from range_core.scenarios import Scenario, ScenarioCatalog


ADMISSION_AUTH = {"Authorization": "Bearer admission-secret"}
TOKEN_MAP = {
    "admission-secret": "admission",
    "instructor-secret": "instructor",
    "red-secret": "red",
}
FLAG = "flag{" + "a" * 32 + "}"


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "admission-01",
            "name": "Admission lifecycle",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "attack_chain": [
                {
                    "id": "exploit-web",
                    "technique": "T1190",
                    "description": "Exploit the target.",
                }
            ],
            "reset_scope": "exercise",
        }
    )


def client(exercise_store, pg_connection, *, actor: str = "admission-secret") -> TestClient:
    return TestClient(
        create_app(
            ScenarioCatalog((scenario(),)),
            exercise_store=exercise_store,
            conn=pg_connection,
            flag_source=FixtureFlagSource(FLAG),
            token_map=TOKEN_MAP,
        ),
        headers={"Authorization": f"Bearer {actor}"},
    )


def prepare(api: TestClient) -> dict:
    response = api.post("/api/exercises/prepare", json={"scenario_id": "admission-01"})
    assert response.status_code == 201
    return response.json()


def test_requested_player_is_absent_until_ready_registration_and_put_is_idempotent(
    exercise_store, pg_connection
) -> None:
    api = client(exercise_store, pg_connection)
    prepared = prepare(api)
    player_url = f"/api/exercises/{prepared['exercise_id']}/players/red-ready"

    assert api.get(player_url).status_code == 404

    first = api.put(
        player_url,
        json={"team": "red", "source_ip": "10.167.30.11"},
    )
    second = api.put(
        player_url,
        json={"team": "red", "source_ip": "10.167.30.11"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert first.json() == {
        "exercise_id": prepared["exercise_id"],
        "player_id": "red-ready",
        "team": "red",
        "source_ip": "10.167.30.11",
        "state": "active",
        "registered_at": first.json()["registered_at"],
        "revoked_at": None,
    }
    assert api.get(player_url).json() == first.json()


def test_blue_ready_identity_has_no_red_source_or_individual_score(
    exercise_store, pg_connection
) -> None:
    api = client(exercise_store, pg_connection)
    prepared = prepare(api)
    blue_url = f"/api/exercises/{prepared['exercise_id']}/players/blue-ready"

    registered = api.put(blue_url, json={"team": "blue"})
    started = client(exercise_store, pg_connection, actor="instructor-secret").post(
        "/api/exercises/start", json={"exercise_id": prepared["exercise_id"]}
    )

    assert registered.status_code == 200
    assert registered.json()["source_ip"] is None
    assert started.status_code == 201
    score = client(exercise_store, pg_connection, actor="red-secret").get("/api/score")
    assert score.status_code == 200
    board = score.json()
    # A blue seat scores nothing individually. Since #36 the response does
    # carry a `blue` board, but it is the team's, aggregated per event —— it
    # has no per-player attribution at all (WS3 spec §5.1, Blue 不個人化), and
    # the blue seat never appears among the red players either.
    assert board["red"]["players"] == []
    assert "players" not in board["blue"]
    assert board["blue"]["events"] == []


def test_scaled_red_seat_is_registered_and_attributed_from_its_tcp_peer(
    exercise_store, pg_connection
) -> None:
    admission = client(exercise_store, pg_connection)
    prepared = prepare(admission)
    player_url = f"/api/exercises/{prepared['exercise_id']}/players/red-sixty"

    registered = admission.put(
        player_url, json={"team": "red", "source_ip": "10.167.30.60"}
    )
    assert registered.status_code == 200
    assert registered.json()["source_ip"] == "10.167.30.60"
    assert client(exercise_store, pg_connection, actor="instructor-secret").post(
        "/api/exercises/start", json={"exercise_id": prepared["exercise_id"]}
    ).status_code == 201

    direct = TestClient(
        admission.app,
        client=("10.167.30.60", 12345),
        headers={"Authorization": "Bearer red-secret"},
    )
    response = direct.post(
        "/api/submissions", json={"objective_id": "capture_flag", "flag": FLAG}
    )

    assert response.status_code == 200
    assert response.json()["player_id"] == "red-sixty"


def test_red_ready_source_uses_tcp_peer_and_revoke_preserves_completion_history(
    exercise_store, pg_connection
) -> None:
    admission = client(exercise_store, pg_connection)
    prepared = prepare(admission)
    player_url = f"/api/exercises/{prepared['exercise_id']}/players/red-ready"
    assert admission.put(
        player_url, json={"team": "red", "source_ip": "10.167.30.11"}
    ).status_code == 200
    assert client(exercise_store, pg_connection, actor="instructor-secret").post(
        "/api/exercises/start", json={"exercise_id": prepared["exercise_id"]}
    ).status_code == 201

    app = admission.app
    direct = TestClient(
        app,
        client=("10.167.30.11", 12345),
        headers={"Authorization": "Bearer red-secret"},
    )
    proxy = TestClient(
        app,
        client=("10.167.30.12", 12345),
        headers={
            "Authorization": "Bearer red-secret",
            "X-Forwarded-For": "10.167.30.11",
            "X-Real-IP": "10.167.30.11",
        },
    )
    submission = {"objective_id": "capture_flag", "flag": FLAG}

    assert proxy.post("/api/submissions", json=submission).status_code == 403
    assert direct.post("/api/submissions", json=submission).status_code == 200
    assert direct.get("/api/score").json()["red"]["players"][0]["total"] == 500

    assert admission.delete(player_url).status_code == 204
    assert admission.delete(player_url).status_code == 204
    assert admission.get(player_url).status_code == 404
    assert direct.post("/api/submissions", json=submission).status_code == 403
    assert direct.get("/api/score").json()["red"]["players"] == []
    assert pg_connection.execute(
        """SELECT count(*) FROM exercise_objective_completions
           WHERE exercise_id = %s AND player_id = 'red-ready'""",
        (prepared["exercise_id"],),
    ).fetchone() == (1,)


def test_admission_lifecycle_scope_fails_closed(exercise_store, pg_connection) -> None:
    app = create_app(
        ScenarioCatalog((scenario(),)),
        exercise_store=exercise_store,
        conn=pg_connection,
        token_map=TOKEN_MAP,
    )

    assert TestClient(app).post(
        "/api/exercises/prepare", json={"scenario_id": "admission-01"}
    ).status_code == 400
    assert TestClient(app, headers={"Authorization": "Bearer instructor-secret"}).post(
        "/api/exercises/prepare", json={"scenario_id": "admission-01"}
    ).status_code == 403
    assert TestClient(app, headers=ADMISSION_AUTH).get("/api/scenarios").status_code == 403


def test_prepare_cannot_replace_an_active_exercise(exercise_store, pg_connection) -> None:
    admission = client(exercise_store, pg_connection)
    prepared = prepare(admission)
    assert admission.put(
        f"/api/exercises/{prepared['exercise_id']}/players/red-ready",
        json={"team": "red", "source_ip": "10.167.30.11"},
    ).status_code == 200
    assert client(exercise_store, pg_connection, actor="instructor-secret").post(
        "/api/exercises/start", json={"exercise_id": prepared["exercise_id"]}
    ).status_code == 201

    replacement = admission.post(
        "/api/exercises/prepare", json={"scenario_id": "admission-01"}
    )

    assert replacement.status_code == 409
