from __future__ import annotations

from fastapi.testclient import TestClient

from admission.api import AdmissionSettings, create_app
from admission.credentials import onsite_code
from admission.store.pool import PoolConfigStore
from admission.store.seats import SeatStore


class RangeFake:
    def __init__(self): self.published = []; self.revoked = []
    def publish_player(self, **value): self.published.append(value)
    def revoke_player(self, exercise_id, player_id): self.revoked.append(player_id)


def client(pg_connection, *, timeout=10):
    return TestClient(create_app(conn=pg_connection, publisher=RangeFake(), settings=AdmissionSettings(
        onsite_secret="site-secret", instructor_tokens={"svc-token": "teacher"},
        request_timeout_seconds=timeout, session_ttl_seconds=3600,
        remote_link_ttl_seconds=600,
    )), base_url="https://testserver")


def claim(c, exercise="EX", team="red"):
    code = onsite_code("site-secret", exercise)
    return c.post(f"/admission/{exercise}/claims", json={"team": team, "onsite_code": code})


def test_claim_reconnect_and_full_pool(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    c = client(pg_connection)
    first = claim(c)
    assert first.status_code == 201
    again = claim(c)
    assert again.status_code == 200
    assert again.json()["seat_id"] == first.json()["seat_id"]
    other = TestClient(c.app, base_url="https://testserver")
    full = claim(other)
    assert full.status_code == 409
    assert full.json()["detail"] == {"code": "team_full", "team": "red", "waitlist": False}


def test_remote_link_is_consumed_once_atomically(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 2, 0)
    c = client(pg_connection)
    _, token = SeatStore(pg_connection).issue_remote_link("EX", 600)
    assert c.post("/admission/EX/claims", json={"team": "red", "remote_token": token}).status_code == 201
    other = TestClient(c.app, base_url="https://testserver")
    assert other.post("/admission/EX/claims", json={"team": "red", "remote_token": token}).status_code == 403


def test_instructor_token_ready_and_auth_request_enforce_endpoint_ownership(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    c = client(pg_connection)
    seat = claim(c).json()["seat_id"]
    denied = c.post(f"/admission/seats/{seat}/ready", json={"endpoints": []})
    assert denied.status_code == 401
    headers = {"Authorization": "Bearer svc-token"}
    endpoint = [{"terminal": "main", "host": "10.167.30.11", "port": 7681}]
    assert c.post(f"/admission/seats/{seat}/ready", headers=headers, json={"endpoints": endpoint}).status_code == 204
    auth = c.get("/admission/auth/ttyd/main")
    assert auth.status_code == 204
    assert auth.headers["x-ttyd-upstream"] == "10.167.30.11:7681"
    assert c.get("/admission/auth/ttyd/wrong").status_code == 403
    assert c.post(f"/admission/seats/{seat}/release", headers=headers).status_code == 204
    assert c.get("/admission/auth/ttyd/main").status_code == 403


def test_ready_rejects_poison_host_and_blue_requires_two_terminals(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 0, 1)
    SeatStore(pg_connection).bulk_build_blue_seats("EX", 1)
    c = client(pg_connection)
    seat = claim(c, team="blue").json()["seat_id"]
    h = {"Authorization": "Bearer svc-token"}
    poison = [{"terminal": "a", "host": "127.0.0.1", "port": 7681}, {"terminal": "b", "host": "10.167.60.12", "port": 7681}]
    assert c.post(f"/admission/seats/{seat}/ready", headers=h, json={"endpoints": poison}).status_code == 422
    wrong_port = [{"terminal": "a", "host": "10.167.60.11", "port": 22}, {"terminal": "b", "host": "10.167.60.12", "port": 7681}]
    assert c.post(f"/admission/seats/{seat}/ready", headers=h, json={"endpoints": wrong_port}).status_code == 422


def test_pending_and_active_require_instructor_token_and_reflect_seat_state(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 2, 0)
    c = client(pg_connection)
    waiting_seat = claim(c).json()["seat_id"]
    other = TestClient(c.app, base_url="https://testserver")
    ready_seat = claim(other).json()["seat_id"]
    headers = {"Authorization": "Bearer svc-token"}
    endpoint = [{"terminal": "main", "host": "10.167.30.11", "port": 7681}]
    c.post(f"/admission/seats/{ready_seat}/ready", headers=headers, json={"endpoints": endpoint})

    assert c.get("/admission/seats/pending?team=red").status_code == 401

    pending = c.get("/admission/seats/pending?team=red", headers=headers)
    assert pending.status_code == 200
    assert [s["seat_id"] for s in pending.json()] == [waiting_seat]

    active = c.get("/admission/seats/active?team=red", headers=headers)
    assert active.status_code == 200
    assert set(active.json()) == {waiting_seat, ready_seat}


def test_unset_production_timeout_is_not_invented(monkeypatch):
    monkeypatch.delenv("ADMISSION_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ADMISSION_SESSION_TTL_SECONDS", raising=False)
    monkeypatch.delenv("ADMISSION_REMOTE_LINK_TTL_SECONDS", raising=False)
    monkeypatch.setenv("ADMISSION_INSTRUCTOR_TOKEN", "token-without-actor")
    monkeypatch.delenv("ADMISSION_INSTRUCTOR_ACTOR", raising=False)
    settings = AdmissionSettings.from_env()
    assert settings.request_timeout_seconds is None
    assert settings.session_ttl_seconds is None
    assert settings.remote_link_ttl_seconds is None
    assert settings.instructor_tokens == {}


def test_logout_revokes_cookie_replay_without_releasing_seat(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    c = client(pg_connection)
    claimed = claim(c).json()
    assert c.post("/admission/logout").status_code == 204
    assert c.get("/admission/auth/ttyd/main").status_code == 403
    assert c.post("/admission/logout").status_code == 401
    seat = SeatStore(pg_connection).get(claimed["seat_id"])
    assert seat["state"] == "requested"
    assert seat["player_id"] == claimed["player_id"]


def test_expired_session_is_rejected_by_auth_and_logout(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    c = client(pg_connection)
    claimed = claim(c).json()
    pg_connection.execute(
        "UPDATE admission_session SET expires_at=now()-interval '1 second' WHERE seat_id=%s",
        (claimed["seat_id"],),
    )
    assert c.get("/admission/auth/ttyd/main").status_code == 403
    assert c.post("/admission/logout").status_code == 401


def test_instructor_audit_uses_explicit_operator_identity(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 2, 0)
    settings = AdmissionSettings(
        onsite_secret="site-secret", instructor_tokens={"token-a": "alice", "token-b": "bob"},
        request_timeout_seconds=10, session_ttl_seconds=3600, remote_link_ttl_seconds=600,
    )
    c = TestClient(create_app(conn=pg_connection, publisher=RangeFake(), settings=settings), base_url="https://testserver")
    first = claim(c).json()
    c.cookies.clear()
    second = claim(c).json()
    assert c.post(f"/admission/seats/{first['seat_id']}/release", headers={"Authorization": "Bearer token-a"}).status_code == 204
    assert c.post(f"/admission/seats/{second['seat_id']}/release", headers={"Authorization": "Bearer token-b"}).status_code == 204
    assert pg_connection.execute("SELECT actor FROM admission_audit ORDER BY audit_id").fetchall() == [("alice",), ("bob",)]


def test_timeout_alert_is_persistent_and_queryable_with_service_token(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    c = client(pg_connection, timeout=10)
    claimed = claim(c).json()
    pg_connection.execute(
        "UPDATE seat SET retry_count=1,requested_at=now()-interval '11 seconds' WHERE seat_id=%s",
        (claimed["seat_id"],),
    )
    headers = {"Authorization": "Bearer svc-token"}
    assert c.post("/admission/maintenance/expire", headers=headers).json() == {"retry": 0, "released": 1}
    response = c.get("/admission/alerts", headers=headers)
    assert response.status_code == 200
    assert response.json() == [{"seat_id": claimed["seat_id"], "reason": "provisioning_timeout"}]


def test_instructor_console_requires_service_token(pg_connection):
    c = client(pg_connection)
    assert c.get("/admission/instructor/EX/console").status_code == 401
    assert c.get(
        "/admission/instructor/EX/console",
        headers={"Authorization": "Bearer svc-token"},
    ).status_code == 200


def test_remote_link_expiry_reuse_and_audited_revoke(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 3, 0)
    c = client(pg_connection)
    headers = {"Authorization": "Bearer svc-token"}
    created = c.post("/admission/EX/remote-links", headers=headers).json()
    token = created["token"]
    assert c.post("/admission/EX/claims", json={"team": "red", "remote_token": token}).status_code == 201
    c.cookies.clear()
    assert c.post("/admission/EX/claims", json={"team": "red", "remote_token": token}).status_code == 403

    expired = c.post("/admission/EX/remote-links", headers=headers).json()
    pg_connection.execute(
        "UPDATE admission_remote_link SET expires_at=now()-interval '1 second' WHERE token_hash=%s",
        (SeatStore._digest(expired["token"]),),
    )
    assert c.post("/admission/EX/claims", json={"team": "red", "remote_token": expired["token"]}).status_code == 403

    revoked = c.post("/admission/EX/remote-links", headers=headers).json()
    assert c.delete(f"/admission/remote-links/{revoked['link_id']}", headers=headers).status_code == 204
    assert c.post("/admission/EX/claims", json={"team": "red", "remote_token": revoked["token"]}).status_code == 403
    assert pg_connection.execute(
        "SELECT actor,action FROM admission_audit WHERE action='revoke_remote_link'"
    ).fetchall() == [("teacher", "revoke_remote_link")]


def test_public_availability_and_selection_disable_full_or_locked_teams(pg_connection):
    pools = PoolConfigStore(pg_connection)
    pools.set_caps_and_prepare_blue("EX", 1, 1)
    c = client(pg_connection)
    assert claim(c, team="red").status_code == 201
    c.cookies.clear()
    pools.lock("EX")

    response = c.get("/admission/EX/availability")
    assert response.status_code == 200
    assert response.json() == {
        "exercise_id": "EX",
        "teams": {
            "red": {"remaining": 0, "disabled": True, "reason": "full"},
            "blue": {"remaining": 0, "disabled": True, "reason": "locked"},
        },
    }
    html = c.get("/admission/EX/join").text.lower()
    assert 'value="red" disabled' in html
    assert 'value="blue" disabled' in html
    for forbidden in ("score", "rank", "objective", "endpoint", "player_id"):
        assert forbidden not in html


def test_prestart_blue_slots_are_capacity_not_provisioned_endpoints(pg_connection):
    PoolConfigStore(pg_connection).set_caps_and_prepare_blue("EX", 0, 2)
    rows = pg_connection.execute(
        "SELECT state,endpoints FROM seat WHERE exercise_id='EX' AND team='blue'"
    ).fetchall()
    assert rows == [("free", []), ("free", [])]
