from pathlib import Path

from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.scenarios import Scenario, ScenarioCatalog


def test_get_scenarios_returns_the_loaded_catalog(tmp_path: Path):
    (tmp_path / "loaded.yaml").write_text(
        """
id: loaded-scenario
name: Loaded Scenario
difficulty: custom-label
duration: 45m
objectives:
  - id: capture_flag
    evaluation: submission
    points: 500
""".strip(),
        encoding="utf-8",
    )
    app = create_app(ScenarioCatalog.from_directory(tmp_path))

    response = TestClient(app).get("/api/scenarios")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "loaded-scenario",
            "name": "Loaded Scenario",
            "difficulty": "custom-label",
            "duration": "45m",
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
            "hints": [],
            "attack_mapping": [],
            "target": None,
            "telemetry": [],
            "detection": [],
        }
    ]


def test_start_and_current_exercise_are_available_over_http(exercise_store) -> None:
    scenario = Scenario.model_validate(
        {
            "id": "sqli-01",
            "name": "SQL Injection",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
        }
    )
    client = TestClient(
        create_app(ScenarioCatalog((scenario,)), exercise_store=exercise_store)
    )

    started = client.post(
        "/api/exercises/start",
        json={
            "scenario_id": "sqli-01",
            "players": [
                {"player_id": "red-alice", "source_ip": "10.30.0.11"},
                {"player_id": "red-bob", "source_ip": "10.30.0.12"},
            ],
        },
    )

    assert started.status_code == 201
    body = started.json()
    assert body["exercise_id"].startswith("ex-")
    assert body["scenario_id"] == "sqli-01"
    assert body["state"] == "running"
    assert body["players"] == [
        {"player_id": "red-alice", "source_ip": "10.30.0.11"},
        {"player_id": "red-bob", "source_ip": "10.30.0.12"},
    ]

    current = client.get("/api/exercises/current")
    assert current.status_code == 200
    assert current.json() == body
