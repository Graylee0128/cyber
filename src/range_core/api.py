"""HTTP interface for Cyber Range Core.

WS5 uses FastAPI rather than P1's intentionally tiny stdlib HTTP shell.  The
Core interface already has nine planned endpoints and #36 adds resumable SSE;
FastAPI provides typed validation and OpenAPI while retaining Starlette's
streaming response support for that later ticket.

Issue #32 adds the start/current exercise lifecycle endpoints. Scoring,
actions, events, reset, and SSE remain outside this module until their tickets.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from range_core.exercises import (
    Exercise,
    ExerciseAlreadyRunning,
    ExerciseStore,
    PlayerRegistration,
    connect,
    ensure_schema,
)
from range_core.scenarios import Scenario, ScenarioCatalog

DEFAULT_SCENARIO_DIRECTORY = Path(__file__).resolve().parents[2] / "scenarios"


class StartExerciseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    players: tuple[PlayerRegistration, ...] = Field(min_length=1)


def create_app(
    catalog: ScenarioCatalog | None = None,
    *,
    exercise_store: ExerciseStore | None = None,
) -> FastAPI:
    """Create the WS5 application with an injected or on-disk catalog."""

    loaded_catalog = catalog or ScenarioCatalog.from_directory(
        DEFAULT_SCENARIO_DIRECTORY
    )
    application = FastAPI(title="Cyber Range Core")

    def provide_exercise_store() -> Iterator[ExerciseStore]:
        if exercise_store is not None:
            yield exercise_store
            return
        conn = connect()
        try:
            ensure_schema(conn)
            yield ExerciseStore(conn)
        finally:
            conn.close()

    @application.get("/api/scenarios", response_model=list[Scenario])
    def list_scenarios() -> list[Scenario]:
        return list(loaded_catalog.scenarios)

    @application.post(
        "/api/exercises/start",
        response_model=Exercise,
        status_code=201,
    )
    def start_exercise(
        request: StartExerciseRequest,
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> Exercise:
        scenario = next(
            (
                candidate
                for candidate in loaded_catalog.scenarios
                if candidate.id == request.scenario_id
            ),
            None,
        )
        if scenario is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        try:
            return store.start(scenario, request.players)
        except ExerciseAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get("/api/exercises/current", response_model=Exercise | None)
    def current_exercise(
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> Exercise | None:
        return store.current()

    return application


app = create_app()
