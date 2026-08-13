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
from range_core.flags import FlagSource, FlagUnavailable, SharedFileFlagSource, matches
from range_core.objectives import HintIndexOutOfRange, HintService, ObjectiveStore, PlayerLookup
from range_core.scenarios import Scenario, ScenarioCatalog
from range_core.scoring import derive_scores
from range_core.telemetry import sync_telemetry_objectives

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


class SubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str = Field(min_length=1)
    flag: str = Field(min_length=1)


class HintRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective_id: str = Field(min_length=1)
    hint_index: int = Field(ge=0)


def _source_ip(request: Request) -> str:
    """The TCP peer address only — **never** `X-Forwarded-For`.

    Source IP is the roster key (#32's six distinct Kali addresses exist
    specifically so an attacker is individually attributable). Honoring a
    client-settable header here would let one red player submit flags or
    request hints as another player. WS7 spec names the same threat for
    clearance and rejects header-trusted source IP for the same reason.
    """
    if request.client is None:
        raise HTTPException(status_code=403, detail="no client address")
    return request.client.host


def create_app(
    catalog: ScenarioCatalog | None = None,
    *,
    exercise_store: ExerciseStore | None = None,
    conn=None,
    flag_source: FlagSource | None = None,
    token_map: Mapping[str, str] | None = None,
) -> FastAPI:
    """Create the WS5 application with an injected or on-disk catalog.

    `conn` is the shared seam for tests: pass the same `pg_connection` used
    to build `exercise_store` so both see the same transaction. Production
    (`conn=None`) opens and closes its own connection per request, same as
    the pre-#33 lifecycle endpoints.

    ``token_map`` maps service token -> identity.  Left unset it is loaded from
    ``RANGE_CORE_TOKEN_<IDENTITY>`` environment variables; an empty map means no
    caller can be identified and every request is rejected (fail closed).
    #52 B2's service identity is a coarser gate than #33's per-player source-IP
    attribution: a token proves the caller is a legitimate range participant
    (e.g. ``red``/``instructor``); source IP then resolves *which* player
    within that team, via ``_player_or_403`` below. Both checks run, in that
    order, on every gameplay endpoint.
    """

    loaded_catalog = catalog or ScenarioCatalog.from_directory(
        DEFAULT_SCENARIO_DIRECTORY
    )
    resolved_flag_source: FlagSource = flag_source or SharedFileFlagSource()
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

    def provide_conn() -> Iterator:
        if conn is not None:
            yield conn
            return
        opened = connect()
        try:
            ensure_schema(opened)
            yield opened
        finally:
            opened.close()

    def provide_exercise_store() -> Iterator[ExerciseStore]:
        if exercise_store is not None:
            yield exercise_store
            return
        for c in provide_conn():
            yield ExerciseStore(c)

    def _running_exercise_and_scenario(store: ExerciseStore) -> tuple[Exercise, Scenario]:
        exercise = store.current()
        if exercise is None:
            raise HTTPException(status_code=404, detail="no running exercise")
        scenario = next(
            (s for s in loaded_catalog.scenarios if s.id == exercise.scenario_id), None
        )
        if scenario is None:
            raise HTTPException(status_code=404, detail="running exercise's scenario not loaded")
        return exercise, scenario

    def _player_or_403(conn, exercise_id: str, source_ip: str) -> str:
        player_id = PlayerLookup(conn).player_for(exercise_id, source_ip)
        if player_id is None:
            raise HTTPException(status_code=403, detail="source IP is not on the exercise roster")
        return player_id

    def _objective_or_404(scenario: Scenario, objective_id: str):
        objective = next((o for o in scenario.objectives if o.id == objective_id), None)
        if objective is None:
            raise HTTPException(status_code=404, detail="unknown objective")
        return objective

    @application.get("/api/scenarios")
    def list_scenarios() -> list[dict]:
        """Full scenario shape **minus hint text** (#33 review finding):
        this is a public, unauthenticated listing and Red can reach it
        directly, so leaving hint text in it makes hint penalties optional
        — nobody needs to call the recorded `POST /api/hints` path when the
        answer is free here. `GET /api/hints?objective_id=` is the sanctioned
        way to see a hint's cost before requesting it; hint *text* is only
        ever returned by `POST /api/hints`, after the usage is recorded."""
        sanitized = []
        for scenario in loaded_catalog.scenarios:
            data = scenario.model_dump()
            data["hints"] = [
                {"objective_id": h["objective_id"], "penalty_percent": h["penalty_percent"]}
                for h in data["hints"]
            ]
            sanitized.append(data)
        return sanitized

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

    @application.post("/api/submissions")
    def submit_flag(
        request: Request,
        body: SubmissionRequest,
        store: ExerciseStore = Depends(provide_exercise_store),
        conn=Depends(provide_conn),
    ) -> dict:
        exercise, scenario = _running_exercise_and_scenario(store)
        player_id = _player_or_403(conn, exercise.exercise_id, _source_ip(request))
        objective = _objective_or_404(scenario, body.objective_id)
        if objective.evaluation != "submission":
            raise HTTPException(
                status_code=409, detail="objective is not a submission-type objective"
            )
        try:
            correct = matches(body.flag, resolved_flag_source)
        except FlagUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not correct:
            return {"accepted": False}
        ObjectiveStore(conn).complete_by_submission(
            exercise.exercise_id, player_id, objective.id, objective=objective
        )
        return {"accepted": True, "objective_id": objective.id, "player_id": player_id}

    @application.get("/api/hints")
    def list_hint_costs(
        objective_id: str,
        store: ExerciseStore = Depends(provide_exercise_store),
        conn=Depends(provide_conn),
    ) -> list[dict]:
        """Penalty per hint index, **never text** — so this endpoint (or any
        future debug/diagnostics path) cannot leak hints for free. Only
        `POST /api/hints` returns text, and only after recording usage."""
        _, scenario = _running_exercise_and_scenario(store)
        _objective_or_404(scenario, objective_id)
        return list(HintService(conn).penalties_for(scenario, objective_id))

    @application.post("/api/hints")
    def request_hint(
        request: Request,
        body: HintRequest,
        store: ExerciseStore = Depends(provide_exercise_store),
        conn=Depends(provide_conn),
    ) -> dict:
        exercise, scenario = _running_exercise_and_scenario(store)
        player_id = _player_or_403(conn, exercise.exercise_id, _source_ip(request))
        _objective_or_404(scenario, body.objective_id)
        try:
            text = HintService(conn).request(
                scenario, exercise.exercise_id, player_id, body.objective_id, body.hint_index
            )
        except HintIndexOutOfRange as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"objective_id": body.objective_id, "hint_index": body.hint_index, "text": text}

    @application.post("/api/objectives/sync")
    def sync_objectives(
        store: ExerciseStore = Depends(provide_exercise_store),
        conn=Depends(provide_conn),
    ) -> dict:
        """Re-scans Core Events and completes any matching telemetry
        objectives. Returns `skipped` reasons so an operator can see *why*
        nothing completed instead of a silent zero (#33 telemetry-path
        deployment gap: no Grafana rule carries an `action_id` label yet)."""
        exercise, scenario = _running_exercise_and_scenario(store)
        result = sync_telemetry_objectives(conn, scenario, exercise.exercise_id)
        return {
            "completed": [
                {"player_id": p, "objective_id": o, "event_id": e}
                for p, o, e in result.completions
            ],
            "skipped": [
                {"event_id": s.event_id, "reason": s.reason} for s in result.skipped
            ],
        }

    @application.get("/api/score")
    def get_score(
        store: ExerciseStore = Depends(provide_exercise_store),
        conn=Depends(provide_conn),
    ) -> dict:
        exercise, scenario = _running_exercise_and_scenario(store)
        sync_telemetry_objectives(conn, scenario, exercise.exercise_id)
        completions = ObjectiveStore(conn).for_exercise(exercise.exercise_id)
        hint_usages = HintService(conn).for_exercise(exercise.exercise_id)
        return derive_scores(scenario, completions, hint_usages).as_dict()

    return application


app = create_app()
