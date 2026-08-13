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
                {
                    "id": "capture_flag",
                    "evaluation": "submission",
                    "points": 500,
                    "telemetry_signal": None,
                }
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


def test_get_scenarios_never_leaks_hint_text(tmp_path: Path):
    """#33 review finding: `/api/scenarios` is unauthenticated and Red can
    reach it directly. If hint text rode along, nobody would ever need the
    recorded `POST /api/hints` path — the penalty would be optional."""
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    package = scenario_root / "hinted-scenario"
    package.mkdir()
    (package / "metadata.yaml").write_text(
        """
id: hinted-scenario
name: Hinted Scenario
difficulty: custom-label
duration: 45m
objectives:
  - id: capture_flag
    evaluation: submission
    points: 500
hints:
  - objective_id: capture_flag
    text: Inspect local application data.
    penalty_percent: 50
targets:
  - host: target-01
    surfaces: [web]
expected_sources: [falco]
attack_chain:
  - id: exploit-web
    technique: T1190
    description: Exploit the target.
reset_scope: exercise
""".strip(),
        encoding="utf-8",
    )
    (package / "briefing.md").write_text("Capture the flag.", encoding="utf-8")
    sources = tmp_path / "sources.yaml"
    sources.write_text("sources:\n  - id: falco\n", encoding="utf-8")
    rules = tmp_path / "rules.yaml"
    rules.write_text("groups: []\n", encoding="utf-8")
    techniques = tmp_path / "techniques.yaml"
    techniques.write_text("techniques:\n  - id: T1190\n", encoding="utf-8")
    catalog = ScenarioCatalog.from_directory(
        scenario_root,
        source_definitions_path=sources,
        detection_rules_path=rules,
        techniques_path=techniques,
    )
    app = create_app(catalog)

    response = TestClient(app).get("/api/scenarios")

    assert response.status_code == 200
    hints = response.json()[0]["hints"]
    assert hints == [{"objective_id": "capture_flag", "penalty_percent": 50}]
    assert "text" not in hints[0]
    assert "Inspect local application data." not in response.text


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
    client = TestClient(
        create_app(ScenarioCatalog((scenario,)), exercise_store=exercise_store)
    )

    started = client.post(
        "/api/exercises/start",
        json={
            "scenario_id": "sqli-01",
            "players": [
                {"player_id": "red-alice", "source_ip": "10.167.30.11"},
                {"player_id": "red-bob", "source_ip": "10.167.30.12"},
            ],
        },
    )

    assert started.status_code == 201
    body = started.json()
    assert body["exercise_id"].startswith("ex-")
    assert body["scenario_id"] == "sqli-01"
    assert body["state"] == "running"
    assert body["players"] == [
        {"player_id": "red-alice", "source_ip": "10.167.30.11"},
        {"player_id": "red-bob", "source_ip": "10.167.30.12"},
    ]

    current = client.get("/api/exercises/current")
    assert current.status_code == 200
    assert current.json() == body

    reset = client.post("/api/exercises/reset")
    assert reset.status_code == 204
    assert client.get("/api/exercises/current").json() is None

    restarted = client.post(
        "/api/exercises/start",
        json={
            "scenario_id": "sqli-01",
            "players": [
                {"player_id": "red-alice", "source_ip": "10.167.30.11"}
            ],
        },
    )
    assert restarted.status_code == 201
    assert restarted.json()["exercise_id"] != body["exercise_id"]

    forbidden_override = client.post(
        "/api/exercises/reset",
        json={"score": 999, "completed_objectives": ["capture_flag"]},
    )
    assert forbidden_override.status_code == 422


def test_get_scenarios_returns_empty_list_during_pre_first_scenario_migration():
    response = TestClient(create_app(ScenarioCatalog(scenarios=()))).get(
        "/api/scenarios"
    )

    assert response.status_code == 200
    assert response.json() == []
