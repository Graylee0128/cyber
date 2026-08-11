"""Scenario definitions and loading for Cyber Range Core.

This package deliberately does not import from :mod:`purple`.  Cyber Range
Core lives in Z-APP while Purple Platform lives in Z-MGMT; the two bounded
contexts communicate through their published contracts instead of sharing
domain models.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ScenarioDefinitionError(ValueError):
    """A scenario file does not satisfy the WS1/WS5 contract."""


class Objective(BaseModel):
    """A narrative Red-team goal, independent from P2 Action Registry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    evaluation: Literal["telemetry", "submission"]
    points: int = Field(gt=0)


class Hint(BaseModel):
    """Optional guidance whose use reduces one objective's score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    penalty_percent: int = Field(gt=0, le=100)


class Target(BaseModel):
    """The deployable target described by a scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(min_length=1)
    service: str = Field(min_length=1)


class Scenario(BaseModel):
    """The immutable content definition for one exercise scenario."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    difficulty: str = Field(min_length=1)
    duration: str = Field(min_length=1)
    objectives: tuple[Objective, ...] = Field(min_length=1)
    hints: tuple[Hint, ...] = ()
    attack_mapping: tuple[str, ...] = ()
    target: Target | None = None
    telemetry: tuple[str, ...] = ()
    detection: tuple[str, ...] = ()

    @classmethod
    def _objective_ids(cls, objectives: tuple[Objective, ...]) -> set[str]:
        return {objective.id for objective in objectives}

    def model_post_init(self, __context: object) -> None:
        objective_ids = self._objective_ids(self.objectives)
        if len(objective_ids) != len(self.objectives):
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


def load_scenario(path: Path | str) -> Scenario:
    """Load and validate one YAML scenario definition."""

    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScenarioDefinitionError(f"{source}: cannot read scenario: {exc}") from exc

    if not isinstance(raw, dict):
        raise ScenarioDefinitionError(f"{source}: scenario root must be a mapping")

    try:
        return Scenario.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioDefinitionError(f"{source}: invalid scenario: {exc}") from exc


@dataclass(frozen=True)
class ScenarioCatalog:
    """An immutable collection of validated scenario definitions."""

    scenarios: tuple[Scenario, ...]

    @classmethod
    def from_directory(cls, directory: Path | str) -> "ScenarioCatalog":
        root = Path(directory)
        files = sorted(root.glob("*.yaml"))
        if not files:
            raise ScenarioDefinitionError(f"{root}: no scenario YAML files found")

        scenarios = tuple(load_scenario(path) for path in files)
        ids = {scenario.id for scenario in scenarios}
        if len(ids) != len(scenarios):
            raise ScenarioDefinitionError(f"{root}: scenario ids must be unique")
        return cls(scenarios=scenarios)
