"""#153 Campaign — `GET /api/scenarios/{id}/briefing`. Closes `ui/README.md`'s
known gap #5: `scenarios/<id>/briefing.md` was a file with no HTTP exit, so
Player Portal could only ever show scenario metadata, never the mission
brief. Same public/unauthenticated tier as `GET /api/scenarios` -- this is
Red's own mission text, not detection/scoring data.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.scenarios import ScenarioCatalog

INSTRUCTOR_TOKEN = "instructor-secret"
RED_TOKEN = "red-secret"
TOKEN_MAP = {INSTRUCTOR_TOKEN: "instructor", RED_TOKEN: "red"}
AUTH = {"Authorization": f"Bearer {INSTRUCTOR_TOKEN}"}
RED_AUTH = {"Authorization": f"Bearer {RED_TOKEN}"}


def _catalog_with_one_scenario(tmp_path: Path, *, scenario_id: str, briefing_text: str):
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    package = scenario_root / scenario_id
    package.mkdir()
    (package / "metadata.yaml").write_text(
        f"""
id: {scenario_id}
name: Briefing Test Scenario
difficulty: custom-label
duration: 30m
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
    (package / "briefing.md").write_text(briefing_text, encoding="utf-8")
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
    return catalog, scenario_root


def test_briefing_returns_the_real_file_content(tmp_path: Path):
    briefing_text = "# 任務簡報：Briefing Test Scenario\n\n## 情境\n\n測試用簡報內容。\n"
    catalog, scenario_root = _catalog_with_one_scenario(
        tmp_path, scenario_id="briefing-test", briefing_text=briefing_text
    )
    app = create_app(catalog, scenario_directory=scenario_root, token_map=TOKEN_MAP)

    response = TestClient(app).get(
        "/api/scenarios/briefing-test/briefing", headers=AUTH
    )

    assert response.status_code == 200
    assert response.json() == {"scenario_id": "briefing-test", "content": briefing_text}


def test_unknown_scenario_id_is_404(tmp_path: Path):
    catalog, scenario_root = _catalog_with_one_scenario(
        tmp_path, scenario_id="briefing-test", briefing_text="content"
    )
    app = create_app(catalog, scenario_directory=scenario_root, token_map=TOKEN_MAP)

    response = TestClient(app).get(
        "/api/scenarios/does-not-exist/briefing", headers=AUTH
    )

    assert response.status_code == 404


def test_missing_bearer_token_is_rejected_like_every_other_endpoint(tmp_path: Path):
    """`require_identity` is an app-wide dependency (api.py:384) -- there is
    no truly unauthenticated route. This endpoint's floor is "any valid
    identity, no elevated clearance," not "no token at all."""
    catalog, scenario_root = _catalog_with_one_scenario(
        tmp_path, scenario_id="briefing-test", briefing_text="content"
    )
    app = create_app(catalog, scenario_directory=scenario_root, token_map=TOKEN_MAP)

    response = TestClient(app).get("/api/scenarios/briefing-test/briefing")

    assert response.status_code == 400


def test_red_clearance_can_read_its_own_mission_briefing(tmp_path: Path):
    """Same tier as `GET /api/scenarios` (test_api_clearance.py): not in
    `ENDPOINT_MIN_CLEARANCE`, so Red (clearance 0) can reach it directly --
    it's Red's own mission brief, not detection/scoring data."""
    catalog, scenario_root = _catalog_with_one_scenario(
        tmp_path, scenario_id="briefing-test", briefing_text="content"
    )
    app = create_app(catalog, scenario_directory=scenario_root, token_map=TOKEN_MAP)

    response = TestClient(app).get(
        "/api/scenarios/briefing-test/briefing", headers=RED_AUTH
    )

    assert response.status_code == 200


def test_real_shipped_scenarios_all_serve_their_briefing():
    """Not synthesized -- hits the real `scenarios/` directory shipped in
    this repo, proving all five Campaign chapters' briefings are servable."""
    app = create_app(token_map=TOKEN_MAP)
    client = TestClient(app)

    listing = client.get("/api/scenarios", headers=AUTH).json()
    assert len(listing) == 5

    for scenario in listing:
        response = client.get(f"/api/scenarios/{scenario['id']}/briefing", headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert body["scenario_id"] == scenario["id"]
        assert body["content"].strip()
