from pathlib import Path

from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.scenarios import ScenarioCatalog


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
