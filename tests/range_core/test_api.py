from pathlib import Path

from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.scenarios import Scenario, ScenarioCatalog


def test_get_scenarios_returns_the_loaded_catalog(tmp_path: Path):
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    package = scenario_root / "loaded-scenario"
    package.mkdir()
    (package / "metadata.yaml").write_text(
        """
id: loaded-scenario
name: Loaded Scenario
difficulty: custom-label
duration: 45m
objectives:
  - id: capture_flag
    evaluation: submission
    points: 500
targets:
  - host: target-01
    surfaces: [web]
expected_sources: [falco]
detection:
  - FalcoCommandExec
intentional_gaps:
  - T1005
attack_chain:
  - id: exploit-web
    technique: T1190
    description: Exploit the public-facing web application.
reset_scope: environment
""".strip(),
        encoding="utf-8",
    )
    (package / "briefing.md").write_text("Capture the flag.", encoding="utf-8")
    sources = tmp_path / "sources.yaml"
    sources.write_text("sources:\n  - id: falco\n", encoding="utf-8")
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        "groups:\n  - rules:\n      - title: FalcoCommandExec\n", encoding="utf-8"
    )
    techniques = tmp_path / "techniques.yaml"
    techniques.write_text(
        "techniques:\n  - id: T1190\n  - id: T1005\n", encoding="utf-8"
    )
    catalog = ScenarioCatalog.from_directory(
        scenario_root,
        source_definitions_path=sources,
        detection_rules_path=rules,
        techniques_path=techniques,
    )
    app = create_app(catalog)

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
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "detection": ["FalcoCommandExec"],
            "intentional_gaps": ["T1005"],
            "attack_chain": [
                {
                    "id": "exploit-web",
                    "technique": "T1190",
                    "description": "Exploit the public-facing web application.",
                }
            ],
            "reset_scope": "environment",
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


def test_get_scenarios_returns_empty_list_during_pre_first_scenario_migration():
    response = TestClient(create_app(ScenarioCatalog(scenarios=()))).get(
        "/api/scenarios"
    )

    assert response.status_code == 200
    assert response.json() == []
