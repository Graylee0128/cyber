from pathlib import Path

from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.scenarios import ScenarioCatalog


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


def test_get_scenarios_returns_empty_list_during_pre_first_scenario_migration():
    response = TestClient(create_app(ScenarioCatalog(scenarios=()))).get(
        "/api/scenarios"
    )

    assert response.status_code == 200
    assert response.json() == []
