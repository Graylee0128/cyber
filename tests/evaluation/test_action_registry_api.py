from fastapi.testclient import TestClient

from purple.evaluation.api import create_app
from purple.store.db import connect
from range_core.scenarios import Scenario, ScenarioCatalog


CATALOG = ScenarioCatalog(
    scenarios=(
        Scenario.model_validate(
            {
                "id": "sqli-01",
                "name": "SQLi",
                "difficulty": "intro",
                "duration": "30m",
                "objectives": [
                    {
                        "id": "o-1",
                        "evaluation": "telemetry",
                        "points": 100,
                        "telemetry_signal": {"action_id": "a-1"},
                    }
                ],
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


def test_registry_can_be_registered_frozen_and_queried(pg_connection):
    client = TestClient(create_app(connect, CATALOG))
    payload = {
        "scenario_id": "sqli-01",
    }
    assert client.post("/api/exercises/ex-api/actions", json=payload).status_code == 201

    frozen = client.post("/api/exercises/ex-api/actions/freeze")
    assert frozen.status_code == 200
    assert frozen.json()["frozen_at"] is not None

    queried = client.get("/api/exercises/ex-api/actions")
    assert queried.status_code == 200
    assert queried.json()["actions"] == [
        {"id": "a-1", "technique": "T1190", "description": "exploit app"}
    ]


def test_unknown_scenario_is_not_found(pg_connection):
    client = TestClient(create_app(connect, CATALOG))
    response = client.post(
        "/api/exercises/ex-bad/actions",
        json={
            "scenario_id": "not-registered",
        },
    )
    assert response.status_code == 404


def test_caller_cannot_fabricate_denominator_actions(pg_connection):
    client = TestClient(create_app(connect, CATALOG))
    response = client.post(
        "/api/exercises/ex-extra/actions",
        json={
            "scenario_id": "sqli-01",
            "actions": [{"id": "fake", "technique": "T1059", "description": "fake"}],
        },
    )
    assert response.status_code == 422
