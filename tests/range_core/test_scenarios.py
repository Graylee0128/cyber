from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from range_core.scenarios import ScenarioCatalog, ScenarioDefinitionError, load_scenario


REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_SCENARIO = {
    "id": "range-chain-01",
    "name": "Range Chain",
    "difficulty": "easy",
    "duration": "30m",
    "objectives": [
        {
            "id": "gain_shell",
            "evaluation": "telemetry",
            "points": 200,
            "telemetry_signal": {"action_id": "gain-shell"},
        },
        {"id": "capture_flag", "evaluation": "submission", "points": 500},
    ],
    "hints": [
        {
            "objective_id": "capture_flag",
            "text": "Inspect local application data.",
            "penalty_percent": 50,
        }
    ],
    "targets": [{"host": "target-01", "surfaces": ["web", "local-shell"]}],
    "expected_sources": ["falco", "alloy"],
    "detection": ["FalcoCommandExec"],
    "intentional_gaps": ["T1005"],
    "attack_chain": [
        {
            "id": "gain-shell",
            "technique": "T1059",
            "description": "Gain a shell on the target.",
        },
        {
            "id": "read-data",
            "technique": "T1005",
            "description": "Read sensitive local data.",
        },
    ],
    "reset_scope": "environment",
}


@pytest.fixture
def reference_paths(tmp_path: Path) -> dict[str, Path]:
    sources = tmp_path / "scenario-sources.yaml"
    sources.write_text(
        yaml.safe_dump({"sources": [{"id": "falco"}, {"id": "alloy"}]}),
        encoding="utf-8",
    )
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        yaml.safe_dump(
            {
                "groups": [
                    {
                        "rules": [
                            {"title": "FalcoCommandExec"},
                            {"title": "FalcoSensitiveFileAccess"},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    techniques = tmp_path / "techniques.yaml"
    techniques.write_text(
        yaml.safe_dump(
            {
                "techniques": [
                    {"id": "T1059"},
                    {"id": "T1005"},
                    {"id": "T1190"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return {
        "source_definitions_path": sources,
        "detection_rules_path": rules,
        "techniques_path": techniques,
    }


def write_package(
    root: Path,
    data: dict | None = None,
    *,
    briefing: str = "Capture the target flag without disrupting the range.",
) -> Path:
    definition = deepcopy(VALID_SCENARIO if data is None else data)
    package = root / definition["id"]
    package.mkdir()
    metadata = package / "metadata.yaml"
    metadata.write_text(yaml.safe_dump(definition, sort_keys=False), encoding="utf-8")
    (package / "briefing.md").write_text(briefing, encoding="utf-8")
    return metadata


def test_empty_scenario_directory_is_a_valid_catalog(tmp_path: Path):
    catalog = ScenarioCatalog.from_directory(tmp_path)

    assert catalog.scenarios == ()


def test_catalog_loads_metadata_from_scenario_directories_without_parsing_briefing(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    write_package(scenario_root, briefing="---\n: this is prose, not YAML")

    catalog = ScenarioCatalog.from_directory(scenario_root, **reference_paths)

    assert [scenario.id for scenario in catalog.scenarios] == ["range-chain-01"]


def test_scenario_models_targets_attack_chain_detection_and_reset_scope(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    scenario = load_scenario(write_package(tmp_path), **reference_paths)

    assert [target.model_dump() for target in scenario.targets] == [
        {"host": "target-01", "surfaces": ("web", "local-shell")}
    ]
    assert scenario.expected_sources == ("falco", "alloy")
    assert scenario.detection == ("FalcoCommandExec",)
    assert scenario.intentional_gaps == ("T1005",)
    assert [action.model_dump() for action in scenario.attack_chain] == [
        {
            "id": "gain-shell",
            "technique": "T1059",
            "description": "Gain a shell on the target.",
        },
        {
            "id": "read-data",
            "technique": "T1005",
            "description": "Read sensitive local data.",
        },
    ]
    assert scenario.reset_scope == "environment"


def test_hint_declares_text_penalty_and_objective_binding(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    scenario = load_scenario(write_package(tmp_path), **reference_paths)

    assert scenario.hints[0].model_dump() == {
        "objective_id": "capture_flag",
        "text": "Inspect local application data.",
        "penalty_percent": 50,
    }


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda data: data.pop("duration"), "duration"),
        (lambda data: data["objectives"][0].update(evaluation="manual"), "evaluation"),
        (lambda data: data["hints"][0].update(objective_id="missing"), "unknown objective"),
        (lambda data: data.pop("reset_scope"), "reset_scope"),
        (lambda data: data.update(reset_scope="target"), "reset_scope"),
    ],
)
def test_invalid_definition_is_rejected_with_a_reason(
    tmp_path: Path,
    reference_paths: dict[str, Path],
    mutate,
    reason: str,
):
    data = deepcopy(VALID_SCENARIO)
    mutate(data)

    with pytest.raises(ScenarioDefinitionError, match=reason):
        load_scenario(write_package(tmp_path, data), **reference_paths)


def test_difficulty_is_a_label_that_changes_no_other_interpretation(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    easy_data = deepcopy(VALID_SCENARIO)
    custom_data = deepcopy(VALID_SCENARIO)
    custom_data["id"] = "range-chain-custom"
    custom_data["difficulty"] = "nightmare"
    easy = load_scenario(write_package(tmp_path, easy_data), **reference_paths)
    custom = load_scenario(write_package(tmp_path, custom_data), **reference_paths)

    assert easy.model_dump(exclude={"id", "difficulty"}) == custom.model_dump(
        exclude={"id", "difficulty"}
    )


def test_objective_rejects_action_registry_relationship_fields(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    data = deepcopy(VALID_SCENARIO)
    data["objectives"][0]["action_id"] = "p2-action-1"

    with pytest.raises(ScenarioDefinitionError, match="action_id"):
        load_scenario(write_package(tmp_path, data), **reference_paths)


def test_scenario_contract_remains_frozen_and_forbids_extra_fields(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    scenario = load_scenario(write_package(tmp_path), **reference_paths)

    assert scenario.model_config["extra"] == "forbid"
    assert scenario.model_config["frozen"] is True
    with pytest.raises(ValidationError, match="frozen"):
        scenario.name = "Mutated"  # type: ignore[misc]


def test_detection_declarations_do_not_change_action_registry_seed(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    first_data = deepcopy(VALID_SCENARIO)
    second_data = deepcopy(VALID_SCENARIO)
    second_data["id"] = "same-actions-more-detection"
    second_data["detection"] = ["FalcoCommandExec", "FalcoSensitiveFileAccess"]
    first = load_scenario(write_package(tmp_path, first_data), **reference_paths)
    second = load_scenario(write_package(tmp_path, second_data), **reference_paths)

    assert first.action_registry_seed() == second.action_registry_seed()
    assert len(first.detection) != len(second.detection)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda data: data.update(expected_sources=["falco", "missing-source"]),
            "missing-source",
        ),
        (
            lambda data: data.update(detection=["MissingGrafanaRule"]),
            "MissingGrafanaRule",
        ),
        (
            lambda data: data["attack_chain"][0].update(technique="T9999"),
            "T9999",
        ),
    ],
)
def test_unknown_platform_references_are_rejected_by_name(
    tmp_path: Path,
    reference_paths: dict[str, Path],
    mutate,
    reason: str,
):
    data = deepcopy(VALID_SCENARIO)
    mutate(data)

    with pytest.raises(ScenarioDefinitionError, match=reason):
        load_scenario(write_package(tmp_path, data), **reference_paths)


def test_intentional_gap_does_not_require_a_same_named_grafana_rule(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    data = deepcopy(VALID_SCENARIO)
    data["detection"] = []
    data["intentional_gaps"] = ["T1005"]

    scenario = load_scenario(write_package(tmp_path, data), **reference_paths)

    assert scenario.intentional_gaps == ("T1005",)


def test_legacy_single_target_and_telemetry_fields_are_rejected(
    tmp_path: Path, reference_paths: dict[str, Path]
):
    data = deepcopy(VALID_SCENARIO)
    data["target"] = {"type": "web", "service": "vulnerable-app"}
    data["telemetry"] = ["app_log"]
    data.pop("targets")
    data.pop("expected_sources")

    with pytest.raises(ScenarioDefinitionError, match="target|telemetry|targets"):
        load_scenario(write_package(tmp_path, data), **reference_paths)


def test_flat_scenario_yaml_is_rejected_with_migration_instruction(tmp_path: Path):
    (tmp_path / "legacy.yaml").write_text("id: legacy", encoding="utf-8")

    with pytest.raises(ScenarioDefinitionError, match="<id>/metadata.yaml"):
        ScenarioCatalog.from_directory(tmp_path)


def test_production_scenario_catalog_loads_without_error():
    # #47 交了 WS2 v1 的唯一一個真 scenario（在此之前這裡刻意是空的，WS2 從零起）。
    # #153 Campaign Pack v1 起改為多條，「恰好一個」不再成立；這裡改成只證
    # catalog 載入不出錯，且 CH1 仍在——每加一條 Campaign chain 在其專屬測試檔
    # 另外釘住它自己在不在（見 tests/scenarios/test_ch2_campus_poster_foothold.py）。
    catalog = ScenarioCatalog.from_directory(REPO_ROOT / "scenarios")

    assert "shopdb-credential-pivot" in [s.id for s in catalog.scenarios]
