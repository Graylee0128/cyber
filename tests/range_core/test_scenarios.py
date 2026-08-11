from __future__ import annotations

from pathlib import Path

import pytest

from range_core.scenarios import ScenarioDefinitionError, load_scenario


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_objectives_declare_evaluation_type_and_points(tmp_path: Path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(
        """
id: sqli-01
name: SQL Injection
difficulty: easy
duration: 30m
objectives:
  - id: discover_endpoint
    evaluation: telemetry
    points: 100
  - id: capture_flag
    evaluation: submission
    points: 500
""".strip(),
        encoding="utf-8",
    )

    scenario = load_scenario(scenario_file)

    assert [objective.model_dump() for objective in scenario.objectives] == [
        {"id": "discover_endpoint", "evaluation": "telemetry", "points": 100},
        {"id": "capture_flag", "evaluation": "submission", "points": 500},
    ]


def test_hint_declares_text_penalty_and_objective_binding(tmp_path: Path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(
        """
id: sqli-01
name: SQL Injection
difficulty: easy
duration: 30m
objectives:
  - id: capture_flag
    evaluation: submission
    points: 500
hints:
  - objective_id: capture_flag
    text: Inspect the application response body.
    penalty_percent: 50
""".strip(),
        encoding="utf-8",
    )

    scenario = load_scenario(scenario_file)

    assert scenario.hints[0].model_dump() == {
        "objective_id": "capture_flag",
        "text": "Inspect the application response body.",
        "penalty_percent": 50,
    }


@pytest.mark.parametrize(
    ("yaml_text", "reason"),
    [
        (
            """
id: missing-duration
name: Missing Duration
difficulty: easy
objectives:
  - id: discover_endpoint
    evaluation: telemetry
    points: 100
""",
            "duration",
        ),
        (
            """
id: bad-evaluation
name: Bad Evaluation
difficulty: easy
duration: 30m
objectives:
  - id: discover_endpoint
    evaluation: manual
    points: 100
""",
            "evaluation",
        ),
        (
            """
id: orphan-hint
name: Orphan Hint
difficulty: easy
duration: 30m
objectives:
  - id: discover_endpoint
    evaluation: telemetry
    points: 100
hints:
  - objective_id: capture_flag
    text: This objective does not exist.
    penalty_percent: 50
""",
            "unknown objective",
        ),
    ],
)
def test_invalid_definition_is_rejected_with_a_reason(
    tmp_path: Path, yaml_text: str, reason: str
):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(yaml_text.strip(), encoding="utf-8")

    with pytest.raises(ScenarioDefinitionError, match=reason):
        load_scenario(scenario_file)


def test_difficulty_is_a_label_that_changes_no_other_interpretation(tmp_path: Path):
    template = """
id: difficulty-is-content
name: Difficulty Is Content
difficulty: {difficulty}
duration: 30m
objectives:
  - id: capture_flag
    evaluation: submission
    points: 500
hints:
  - objective_id: capture_flag
    text: Inspect the response body.
    penalty_percent: 50
"""
    easy_file = tmp_path / "easy.yaml"
    custom_file = tmp_path / "custom.yaml"
    easy_file.write_text(template.format(difficulty="easy").strip(), encoding="utf-8")
    custom_file.write_text(
        template.format(difficulty="nightmare").strip(), encoding="utf-8"
    )

    easy = load_scenario(easy_file)
    custom = load_scenario(custom_file)

    assert easy.model_dump(exclude={"difficulty"}) == custom.model_dump(
        exclude={"difficulty"}
    )


def test_objective_rejects_action_registry_relationship_fields(tmp_path: Path):
    scenario_file = tmp_path / "scenario.yaml"
    scenario_file.write_text(
        """
id: no-action-registry-link
name: No Action Registry Link
difficulty: easy
duration: 30m
objectives:
  - id: trigger_sqli
    evaluation: telemetry
    points: 200
    action_id: p2-action-1
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ScenarioDefinitionError, match="action_id"):
        load_scenario(scenario_file)


def test_shipped_sqli_scenario_expresses_the_sa_section_11_draft():
    scenario = load_scenario(REPO_ROOT / "scenarios" / "sqli-01.yaml")

    assert scenario.model_dump(mode="json") == {
        "id": "sqli-01",
        "name": "SQL Injection",
        "difficulty": "easy",
        "duration": "30m",
        "objectives": [
            {"id": "discover_endpoint", "evaluation": "telemetry", "points": 100},
            {"id": "trigger_sqli", "evaluation": "telemetry", "points": 200},
            {"id": "capture_flag", "evaluation": "submission", "points": 500},
        ],
        "hints": [
            {
                "objective_id": "discover_endpoint",
                "text": "Inspect the application routes exposed by the target.",
                "penalty_percent": 25,
            },
            {
                "objective_id": "capture_flag",
                "text": "Inspect the response body after the injection succeeds.",
                "penalty_percent": 50,
            },
        ],
        "attack_mapping": ["T1190"],
        "target": {"type": "web", "service": "vulnerable-app"},
        "telemetry": ["app_log", "http_metric"],
        "detection": ["SQLInjectionBurst"],
    }
