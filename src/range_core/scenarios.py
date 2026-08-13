"""Scenario definitions and loading for Cyber Range Core.

This package deliberately does not import from :mod:`purple`.  Cyber Range
Core lives in Z-APP while Purple Platform lives in Z-MGMT; the two bounded
contexts communicate through their published contracts instead of sharing
domain models.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DEFINITIONS_PATH = REPO_ROOT / "config" / "scenario-sources.yaml"
DEFAULT_DETECTION_RULES_PATH = (
    REPO_ROOT / "deploy" / "grafana" / "provisioning" / "alerting" / "rules.yaml"
)
DEFAULT_TECHNIQUES_PATH = REPO_ROOT / "config" / "techniques.yaml"
NonEmptyText = Annotated[str, Field(min_length=1)]


class ScenarioDefinitionError(ValueError):
    """A scenario package does not satisfy the WS1/WS2/WS5 contract."""


class TelemetrySignal(BaseModel):
    """The Core Event that completes a `telemetry` objective (#33).

    `action_id` references this same scenario's `attack_chain`, never a
    technique or event_type — technique+proximity matching is exactly what
    `purple.store.events.CoreEventStore.detections_by_action` refuses to do:
    "技法與時間窗鄰近性在這裡完全不參與"／"沒帶關聯鍵的事件不得被拿去對應任何
    註冊動作，這正是本方法只走 action_id 的理由". One technique can back
    multiple registered actions; guessing "the nearest one" attributes
    evidence to the wrong action silently.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: NonEmptyText


class Objective(BaseModel):
    """A narrative Red-team goal, independent from P2 Action Registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyText
    evaluation: Literal["telemetry", "submission"]
    points: int = Field(gt=0)
    telemetry_signal: TelemetrySignal | None = None


class Hint(BaseModel):
    """Optional guidance whose use reduces one objective's score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: NonEmptyText
    text: NonEmptyText
    penalty_percent: int = Field(gt=0, le=100)


class Target(BaseModel):
    """One range host and the attack surfaces a scenario requires on it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: NonEmptyText
    surfaces: tuple[NonEmptyText, ...] = Field(min_length=1)


class AttackAction(BaseModel):
    """One expected Red-team action used to seed P2 Action Registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyText
    technique: NonEmptyText
    description: NonEmptyText


class Scenario(BaseModel):
    """The immutable content definition for one exercise scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: NonEmptyText
    name: NonEmptyText
    difficulty: NonEmptyText
    duration: NonEmptyText
    objectives: tuple[Objective, ...] = Field(min_length=1)
    hints: tuple[Hint, ...] = ()
    targets: tuple[Target, ...] = Field(min_length=1)
    expected_sources: tuple[NonEmptyText, ...] = Field(min_length=1)
    detection: tuple[NonEmptyText, ...] = ()
    intentional_gaps: tuple[NonEmptyText, ...] = ()
    attack_chain: tuple[AttackAction, ...] = Field(min_length=1)
    reset_scope: Literal["exercise", "environment"]

    def model_post_init(self, __context: object) -> None:
        objective_ids = [objective.id for objective in self.objectives]
        if len(set(objective_ids)) != len(objective_ids):
            raise ValueError("objective ids must be unique")
        missing = sorted(
            {
                hint.objective_id
                for hint in self.hints
                if hint.objective_id not in objective_ids
            }
        )
        if missing:
            raise ValueError(f"hint references unknown objective(s): {', '.join(missing)}")

        target_hosts = [target.host for target in self.targets]
        if len(set(target_hosts)) != len(target_hosts):
            raise ValueError("target hosts must be unique")
        action_ids = [action.id for action in self.attack_chain]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("attack action ids must be unique")
        if len(set(self.expected_sources)) != len(self.expected_sources):
            raise ValueError("expected_sources must be unique")
        if len(set(self.detection)) != len(self.detection):
            raise ValueError("detection rules must be unique")
        if len(set(self.intentional_gaps)) != len(self.intentional_gaps):
            raise ValueError("intentional_gaps must be unique")

        action_id_set = set(action_ids)
        for objective in self.objectives:
            if objective.evaluation == "telemetry" and objective.telemetry_signal is None:
                raise ValueError(
                    f"objective {objective.id!r}: telemetry objectives require telemetry_signal"
                )
            if objective.evaluation == "submission" and objective.telemetry_signal is not None:
                raise ValueError(
                    f"objective {objective.id!r}: submission objectives must not set telemetry_signal"
                )
            if (
                objective.telemetry_signal is not None
                and objective.telemetry_signal.action_id not in action_id_set
            ):
                raise ValueError(
                    f"objective {objective.id!r}: telemetry_signal references unknown "
                    f"attack_chain action {objective.telemetry_signal.action_id!r}"
                )

        submission_objective_ids = [
            objective.id for objective in self.objectives if objective.evaluation == "submission"
        ]
        if len(submission_objective_ids) > 1:
            # #33: `flags.py`'s `FlagSource` is one rotated value for the
            # whole environment (#45 tracks per-objective keyed flags). A
            # second `submission` objective today would share that same
            # expected value, so any correct submission would complete
            # *every* submission objective, not just the one it was for.
            # Temporary until #45 lands; then this check can widen or drop.
            raise ValueError(
                "scenario has more than one submission-type objective "
                f"({', '.join(submission_objective_ids)}): the shared environment "
                "flag (#45) can't distinguish which one a submission is for"
            )

    def action_registry_seed(self) -> tuple[AttackAction, ...]:
        """Return #21 seed actions; detection declarations are not a denominator."""
        return self.attack_chain

    def hints_for(self, objective_id: str) -> tuple[Hint, ...]:
        """Hints for one objective, in scenario-file declaration order.

        The single definition of ``hint_index`` (#33): it is the position
        within this tuple, not a sort key. A later "sort hints for display"
        change must not silently re-map every historical usage record.
        """
        return tuple(hint for hint in self.hints if hint.objective_id == objective_id)


@dataclass(frozen=True)
class _ReferenceCatalog:
    source_ids: frozenset[str]
    detection_rules: frozenset[str]
    technique_ids: frozenset[str]


def _read_mapping(path: Path | str, label: str) -> dict:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScenarioDefinitionError(f"{source}: cannot read {label}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ScenarioDefinitionError(f"{source}: {label} root must be a mapping")
    return raw


def _mapping_list(raw: dict, key: str, source: Path | str) -> list[dict]:
    items = raw.get(key)
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ScenarioDefinitionError(f"{source}: {key} must be a list of mappings")
    return items


def _load_references(
    source_definitions_path: Path | str,
    detection_rules_path: Path | str,
    techniques_path: Path | str,
) -> _ReferenceCatalog:
    sources_raw = _read_mapping(source_definitions_path, "source definitions")
    source_ids = frozenset(
        item.get("id")
        for item in _mapping_list(sources_raw, "sources", source_definitions_path)
        if isinstance(item.get("id"), str) and item["id"]
    )

    rules_raw = _read_mapping(detection_rules_path, "Grafana detection rules")
    rule_names: set[str] = set()
    for group in _mapping_list(rules_raw, "groups", detection_rules_path):
        rules = group.get("rules")
        if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
            raise ScenarioDefinitionError(
                f"{detection_rules_path}: each Grafana group.rules must be a list of mappings"
            )
        rule_names.update(
            rule["title"]
            for rule in rules
            if isinstance(rule.get("title"), str) and rule["title"]
        )

    techniques_raw = _read_mapping(techniques_path, "technique whitelist")
    technique_ids = frozenset(
        item.get("id")
        for item in _mapping_list(techniques_raw, "techniques", techniques_path)
        if isinstance(item.get("id"), str) and item["id"]
    )
    return _ReferenceCatalog(source_ids, frozenset(rule_names), technique_ids)


def _validate_references(
    source: Path, scenario: Scenario, references: _ReferenceCatalog
) -> None:
    unknown_sources = sorted(set(scenario.expected_sources) - references.source_ids)
    if unknown_sources:
        raise ScenarioDefinitionError(
            f"{source}: unknown expected source(s): {', '.join(unknown_sources)}"
        )

    unknown_rules = sorted(set(scenario.detection) - references.detection_rules)
    if unknown_rules:
        raise ScenarioDefinitionError(
            f"{source}: unknown detection rule(s): {', '.join(unknown_rules)}"
        )

    unknown_techniques = sorted(
        (
            {action.technique for action in scenario.attack_chain}
            | set(scenario.intentional_gaps)
        )
        - references.technique_ids
    )
    if unknown_techniques:
        raise ScenarioDefinitionError(
            f"{source}: unknown attack-chain technique(s): {', '.join(unknown_techniques)}"
        )


def _load_scenario(source: Path, references: _ReferenceCatalog) -> Scenario:
    if source.name != "metadata.yaml":
        raise ScenarioDefinitionError(f"{source}: scenario metadata must be named metadata.yaml")
    briefing = source.with_name("briefing.md")
    if not briefing.is_file():
        raise ScenarioDefinitionError(f"{source.parent}: missing briefing.md")

    raw = _read_mapping(source, "scenario")
    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioDefinitionError(f"{source}: invalid scenario: {exc}") from exc
    if scenario.id != source.parent.name:
        raise ScenarioDefinitionError(
            f"{source}: scenario id {scenario.id!r} must match directory {source.parent.name!r}"
        )
    _validate_references(source, scenario, references)
    return scenario


def load_scenario(
    path: Path | str,
    *,
    source_definitions_path: Path | str = DEFAULT_SOURCE_DEFINITIONS_PATH,
    detection_rules_path: Path | str = DEFAULT_DETECTION_RULES_PATH,
    techniques_path: Path | str = DEFAULT_TECHNIQUES_PATH,
) -> Scenario:
    """Load one ``scenarios/<id>/metadata.yaml`` package and validate references."""
    references = _load_references(
        source_definitions_path, detection_rules_path, techniques_path
    )
    return _load_scenario(Path(path), references)


@dataclass(frozen=True)
class ScenarioCatalog:
    """An immutable collection of validated scenario definitions."""

    scenarios: tuple[Scenario, ...]

    @classmethod
    def from_directory(
        cls,
        directory: Path | str,
        *,
        source_definitions_path: Path | str = DEFAULT_SOURCE_DEFINITIONS_PATH,
        detection_rules_path: Path | str = DEFAULT_DETECTION_RULES_PATH,
        techniques_path: Path | str = DEFAULT_TECHNIQUES_PATH,
    ) -> "ScenarioCatalog":
        root = Path(directory)
        if not root.is_dir():
            raise ScenarioDefinitionError(f"{root}: scenario directory not found")
        legacy_files = sorted(root.glob("*.yaml"))
        if legacy_files:
            raise ScenarioDefinitionError(
                f"{legacy_files[0]}: flat scenario YAML is no longer supported; "
                "use <id>/metadata.yaml"
            )
        packages = sorted(path for path in root.iterdir() if path.is_dir())
        if not packages:
            return cls(scenarios=())

        references = _load_references(
            source_definitions_path, detection_rules_path, techniques_path
        )
        scenarios: list[Scenario] = []
        for package in packages:
            metadata = package / "metadata.yaml"
            if not metadata.is_file():
                raise ScenarioDefinitionError(f"{package}: missing metadata.yaml")
            scenarios.append(_load_scenario(metadata, references))

        ids = {scenario.id for scenario in scenarios}
        if len(ids) != len(scenarios):
            raise ScenarioDefinitionError(f"{root}: scenario ids must be unique")
        return cls(scenarios=tuple(scenarios))
