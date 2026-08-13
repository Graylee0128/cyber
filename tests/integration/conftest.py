"""A real running exercise for receiver-driven compose integration tests."""

from __future__ import annotations

import pytest

from range_core.exercises import ExerciseStore, PlayerRegistration
from range_core.scenarios import Scenario


PIPELINE_CONTRACT_SCENARIO = Scenario.model_validate(
    {
        "id": "sqli-01",
        "name": "SQLi pipeline contract fixture",
        "difficulty": "test",
        "duration": "30m",
        "objectives": [
            {"id": "observe-sqli", "evaluation": "telemetry", "points": 1}
        ],
        "targets": [{"host": "compose-vulnerable-app", "surfaces": ["web"]}],
        "expected_sources": ["alloy"],
        "detection": ["SQLInjectionBurst"],
        "attack_chain": [
            {
                "id": "exercise-sqli-pipeline",
                "technique": "T1190",
                "description": "Exercise the compose SQLi telemetry path.",
            }
        ],
        "reset_scope": "exercise",
    }
)


@pytest.fixture(autouse=True)
def running_exercise(pg_connection):
    return ExerciseStore(pg_connection).start(
        PIPELINE_CONTRACT_SCENARIO,
        (
            PlayerRegistration(
                player_id="integration-red", source_ip="10.167.30.11"
            ),
        ),
    )
