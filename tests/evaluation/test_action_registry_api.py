from fastapi.testclient import TestClient

from purple.evaluation.api import create_app
from purple.store.db import connect


def test_registry_can_be_registered_frozen_and_queried(pg_connection):
    client = TestClient(create_app(connect))
    payload = {
        "scenario_id": "sqli-01",
        "actions": [
            {"id": "a-1", "technique": "T1190", "description": "exploit app"}
        ],
    }
    assert client.post("/api/exercises/ex-api/actions", json=payload).status_code == 201

    frozen = client.post("/api/exercises/ex-api/actions/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["frozen_at"] is not None

    queried = client.get("/api/exercises/ex-api/actions")
    assert queried.status_code == 200
    assert queried.json()["actions"] == payload["actions"]


def test_unknown_technique_is_an_explicit_conflict(pg_connection):
    client = TestClient(create_app(connect))
    response = client.post(
        "/api/exercises/ex-bad/actions",
        json={
            "scenario_id": "sqli-01",
            "actions": [{"id": "a-1", "technique": "T9999", "description": "bad"}],
        },
    )
    assert response.status_code == 409
    assert "T9999" in response.json()["detail"]
