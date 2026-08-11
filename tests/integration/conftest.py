"""A real running exercise for receiver-driven compose integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from range_core.exercises import ExerciseStore, PlayerRegistration
from range_core.scenarios import load_scenario


SCENARIO = Path(__file__).resolve().parents[2] / "scenarios" / "sqli-01.yaml"


@pytest.fixture(autouse=True)
def running_exercise(pg_connection):
    return ExerciseStore(pg_connection).start(
        load_scenario(SCENARIO),
        (PlayerRegistration(player_id="integration-red", source_ip="10.30.0.11"),),
    )
