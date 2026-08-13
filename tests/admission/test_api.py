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
        onsite_secret="site-secret", instructor_tokens={"svc-token": "teacher"}, request_timeout_seconds=timeout
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
    token = SeatStore(pg_connection).issue_remote_link("EX")
    assert c.post("/admission/EX/claims", json={"team": "red", "remote_token": token}).status_code == 201
    other = TestClient(c.app, base_url="https://testserver")
    assert other.post("/admission/EX/claims", json={"team": "red", "remote_token": token}).status_code == 403


def test_instructor_token_ready_and_auth_request_enforce_endpoint_ownership(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    c = client(pg_connection)
    seat = claim(c).json()["seat_id"]
    denied = c.post(f"/admission/seats/{seat}/ready", json={"endpoints": []})
    assert denied.status_code == 403
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


def test_unset_production_timeout_is_not_invented(monkeypatch):
    monkeypatch.delenv("ADMISSION_REQUEST_TIMEOUT_SECONDS", raising=False)
    settings = AdmissionSettings.from_env()
    assert settings.request_timeout_seconds is None
