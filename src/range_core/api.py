"""HTTP interface for Cyber Range Core.

WS5 uses FastAPI rather than P1's intentionally tiny stdlib HTTP shell.  The
Core interface already has nine planned endpoints and #36 adds resumable SSE;
FastAPI provides typed validation and OpenAPI while retaining Starlette's
streaming response support for that later ticket.

Issue #32 adds the start/current/reset exercise lifecycle endpoints. Scoring,
actions, events, and SSE remain outside this module until their tickets.

Issue #52 B2 makes the caller identity come from a deployment-injected service
token (``Authorization: Bearer <token>``) instead of anything the caller can
fill in.  The exchange itself lives in ``disclosure.identity`` and is shared
with the Evidence API: one rule, two exits.  ``range_core`` still does not
import ``purple`` -- both sides import the shared contract package.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping
from pathlib import Path

from disclosure import extract_token, load_service_tokens, resolve_identity
from fastapi import Depends, FastAPI, HTTPException, Request
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

log = logging.getLogger("range_core.api")

DEFAULT_SCENARIO_DIRECTORY = Path(__file__).resolve().parents[2] / "scenarios"

#: This service's own token namespace.  The exchange logic is shared with the
#: Evidence API, the tokens are not: an Evidence token must not buy a Range Core
#: identity, so a leak on one exit stays on that exit.
TOKEN_ENV_PREFIX = "RANGE_CORE_TOKEN_"


class StartExerciseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    players: tuple[PlayerRegistration, ...] = Field(min_length=1)


class ResetExerciseRequest(BaseModel):
    """Intentionally empty: reset accepts no score or completion overrides."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def create_app(
    catalog: ScenarioCatalog | None = None,
    *,
    exercise_store: ExerciseStore | None = None,
    token_map: Mapping[str, str] | None = None,
) -> FastAPI:
    """Create the WS5 application with an injected or on-disk catalog.

    ``token_map`` maps service token -> identity.  Left unset it is loaded from
    ``RANGE_CORE_TOKEN_<IDENTITY>`` environment variables; an empty map means no
    caller can be identified and every request is rejected (fail closed).
    """

    loaded_catalog = catalog or ScenarioCatalog.from_directory(
        DEFAULT_SCENARIO_DIRECTORY
    )
    tokens = dict(
        load_service_tokens(TOKEN_ENV_PREFIX, os.environ)
        if token_map is None
        else token_map
    )
    if not tokens:
        log.warning(
            "no %s* configured; this service will reject every request",
            TOKEN_ENV_PREFIX,
        )

    def require_identity(request: Request) -> str:
        """Resolve the caller from its bearer token, or refuse to serve it.

        The identity is never read from a caller-supplied field, so claiming to
        be ``purple`` or ``instructor`` buys nothing.  Declared as an app-wide
        dependency rather than per route: a new endpoint is protected by
        default, and forgetting to opt in cannot silently reopen the hole.
        The token itself is never echoed back nor logged.
        """
        token = extract_token(request.headers)
        if not token:
            raise HTTPException(
                status_code=400, detail="missing Authorization bearer token"
            )
        identity = resolve_identity(token, tokens)
        if identity is None:
            raise HTTPException(
                status_code=403, detail="unknown or invalid service token"
            )
        return identity

    application = FastAPI(
        title="Cyber Range Core",
        dependencies=[Depends(require_identity)],
    )

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

    @application.post("/api/exercises/reset", status_code=204)
    def reset_exercise(
        request: ResetExerciseRequest | None = None,
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> None:
        del request
        if store.reset_current() is None:
            raise HTTPException(status_code=404, detail="no running exercise")

    return application


app = create_app()
