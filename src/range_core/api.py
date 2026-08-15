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

import asyncio
import logging
import os
import socket
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path

import psycopg
from disclosure import (
    CALLER_CLEARANCE,
    extract_token,
    load_service_tokens,
    resolve_identity,
)
from disclosure.detection_rules import load_rule_titles
from disclosure.fields import FIELD_MASKING
from disclosure import build_label_map
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from range_core.blue_action_store import (
    AlreadyJudged,
    BlueActionStore,
    StoredDispatchOutcome,
    StoredExecutionEvidence,
    UnknownEvent,
)
from range_core.blue_actions import BlueActionRejected, BlueActionType, MappingTechniqueTruth
from range_core.blue_scoring import BlueScoringConfig, derive_blue_scores
from range_core.response_dispatch import HttpResponseDispatcher, ResponseDispatcher
from range_core.event_stream import (
    CoreEventStream,
    comment_frame,
    frames_for,
    parse_last_event_id,
)

from range_core.exercises import (
    AdmissionPlayer,
    AdmissionPlayerRegistration,
    Exercise,
    ExerciseAlreadyRunning,
    ExerciseNotPrepared,
    ExerciseStore,
    PlayerRegistrationConflict,
    PrepareExercise,
    PlayerRegistration,
    connect,
    ensure_schema,
)
from range_core.flags import (
    FlagSource,
    FlagUnavailable,
    SharedFileFlagSource,
    is_valid_flag_shape,
    matches,
)
from range_core.objectives import (
    Clock as ActionClock,
    HintIndexOutOfRange,
    HintService,
    ObjectiveStore,
    PlayerLookup,
    SystemClock as ActionSystemClock,
)
from range_core.scenarios import Scenario, ScenarioCatalog
from range_core.scoring import derive_scores
from range_core.telemetry import sync_telemetry_objectives

log = logging.getLogger("range_core.api")

DEFAULT_SCENARIO_DIRECTORY = Path(__file__).resolve().parents[2] / "scenarios"

#: This service's own token namespace.  The exchange logic is shared with the
#: Evidence API, the tokens are not: an Evidence token must not buy a Range Core
#: identity, so a leak on one exit stays on that exit.
TOKEN_ENV_PREFIX = "RANGE_CORE_TOKEN_"

#: Endpoints that need more than "any valid participant token" (#49, raised
#: reviewing #33 against #52 B2).  B2 only ever promised that identity cannot be
#: self-reported; it deliberately left *which identity may call what* to WS5.
#: #33 makes that concrete: source-IP attribution means the Kali hosts talk to
#: Range Core directly, so a red player's machine holds a red token -- and with
#: no policy here, that token also resets the running exercise and wipes every
#: recorded objective completion.
#:
#: Data, not scattered ifs: a new privileged endpoint is one row.  Endpoints
#: absent from the table need any valid token, which is the gameplay default
#: (submissions and hints are exactly what red players are supposed to call).
#: SSE 的輪詢間隔與 keep-alive 間隔（#36）。輪詢而非 LISTEN/NOTIFY：一場演練的
#: 事件數以個位數計，200ms 的輪詢對 Postgres 是零負擔，而 LISTEN 會把「誰負責
#: 通知」這件事塞進 P1 的寫入路徑 —— 那是為了不存在的規模付跨區耦合的錢。
POLL_INTERVAL_S = 0.2
#: 中介設備會砍掉太久沒有位元組的長連線；SSE 註解行不是事件，不動 Last-Event-ID。
KEEPALIVE_INTERVAL_S = 15.0
#: 一條串流連線的壽命上限（#36）。到期就自然結束，不是錯誤 —— 瀏覽器的
#: EventSource 收到連線關閉會帶著上次的 `Last-Event-ID` 自動重連，續傳機制本來
#: 就是為這件事存在的。**這是唯一保證每條連線都會結束的機制**：偵測「客戶端斷線」
#: 依賴 ASGI 層的 `request.is_disconnected()`，那條路徑值得留著當優化（早點放手
#: 資源），但不能是唯一的出口 —— 沒有它，一個沒有正常收到斷線訊號的連線（某些
#: reverse proxy／測試環境會吃掉這個訊號）就會佔著一條資料庫連線耗到演練結束。
DEFAULT_MAX_STREAM_SECONDS = 300.0

ENDPOINT_MIN_CLEARANCE: dict[tuple[str, str], int] = {
    ("POST", "/api/exercises/start"): CALLER_CLEARANCE["instructor"],
    ("POST", "/api/exercises/reset"): CALLER_CLEARANCE["instructor"],
    # Blue Actions carry no per-user identity (WS3 spec §5.1 — "who" is
    # always the team). Gating the endpoint at blue clearance is therefore
    # the *only* enforcement that a red player's token can't submit them.
    ("POST", "/api/blue-actions"): CALLER_CLEARANCE["blue"],
}


class StartExerciseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str | None = Field(default=None, min_length=1)
    exercise_id: str | None = Field(default=None, min_length=1)
    players: tuple[PlayerRegistration, ...] = ()


class PrepareExerciseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)


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


class BlueActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    technique: str | None = None


#: Header carrying the seat's real source IP, set by the Product UI gateway
#: only (#126 item 4). Deliberately not `X-Forwarded-For`: that name is set by
#: every proxy in the world and by plenty of clients, so a value under it says
#: nothing about who put it there. This one is meaningful only in combination
#: with the peer check in `_source_ip` -- the gateway strips any inbound copy
#: before setting its own.
SEAT_SOURCE_IP_HEADER = "X-Seat-Source-Ip"


def _trusted_gateway_addresses() -> set[str]:
    """Resolve the configured gateway hostname to the addresses it may call from.

    Resolved per request rather than cached at startup: in compose and in a
    real deployment the gateway's address can change when it restarts, and a
    stale cache would fail closed in a way that looks like a roster bug.
    """
    host = os.environ.get("RANGE_CORE_TRUSTED_EDGE_HOST", "").strip()
    if not host:
        return set()
    return _resolve_host(host)


def _resolve_host(host: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        # Unresolvable trusted host = no trusted addresses = fail closed.
        return set()


def _source_ip(request: Request) -> str:
    """The TCP peer address, **except** when the peer is the trusted gateway.

    Source IP is the roster key (#32's six distinct Kali addresses exist
    specifically so an attacker is individually attributable). Honoring a
    client-settable header unconditionally would let one red player submit
    flags or request hints as another player. WS7 spec names the same threat
    for clearance and rejects header-trusted source IP for the same reason.

    #126 item 4: a Player Portal served through a reverse proxy makes the peer
    address the *proxy's*, so every submission/hint went 403 at once. The fix
    is not to start trusting a header -- it is to trust exactly one caller:

        peer is the Product UI gateway -> the seat IP it resolved via Admission
        peer is anything else          -> the peer address, header ignored

    Why this is not the rejected design: the header is only consulted after the
    TCP peer has been checked against the resolved address of the deployment's
    gateway. A red player connecting to Range Core directly and setting the
    header gets their own peer address anyway, because their peer is a Kali
    host and not the gateway. Forging it requires already being the gateway.

    Why the gateway and not Z-EDGE, which also fronts players: claiming a
    source IP on someone's behalf requires holding a Range Core service token,
    and Z-EDGE must stay credential-free (WS8 spec §5.3 -- compromising it must
    not hand over any token). The gateway already holds every service token by
    design, so this adds no new secret to any host.

    The gateway, for its part, must strip any client-supplied copy of the
    header before setting its own (see deploy/ui/default.conf.template) --
    otherwise a browser could send one through and have it forwarded verbatim.
    """
    if request.client is None:
        raise HTTPException(status_code=403, detail="no client address")
    peer = request.client.host
    forwarded = request.headers.get(SEAT_SOURCE_IP_HEADER)
    if forwarded and peer in _trusted_gateway_addresses():
        return forwarded
    return peer


def create_app(
    catalog: ScenarioCatalog | None = None,
    *,
    exercise_store: ExerciseStore | None = None,
    conn=None,
    flag_source: FlagSource | None = None,
    token_map: Mapping[str, str] | None = None,
    max_stream_seconds: float = DEFAULT_MAX_STREAM_SECONDS,
    action_clock: ActionClock | None = None,
    response_dispatcher_factory: Callable[[psycopg.Connection], ResponseDispatcher] | None = None,
) -> FastAPI:
    """Create the WS5 application with an injected or on-disk catalog.

    `conn` is the shared seam for tests: pass the same `pg_connection` used
    to build `exercise_store` so both see the same transaction. Production
    (`conn=None`) opens and closes its own connection per request, same as
    the pre-#33 lifecycle endpoints.

    ``max_stream_seconds`` bounds how long a single `/api/events/live`
    connection stays open before closing on its own (see the constant's
    docstring). Tests pass a small value so a stream test's own connection
    always terminates deterministically, independent of whether the test
    transport actually propagates client disconnection.

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
    # ``admission`` is an API scope, not a player disclosure clearance. Keep
    # it out of CALLER_CLEARANCE while using the same fail-closed token seam.
    if token_map is None:
        admission_token = os.environ.get(f"{TOKEN_ENV_PREFIX}ADMISSION")
        if admission_token:
            tokens[admission_token] = "admission"
    # #49 的 rule 匿名標籤。**與 Evidence API 共用同一份排序來源**，否則同一條
    # 規則在 SSE 與 Evidence 兩個畫面會是不同號碼，藍隊會以為那是兩條規則。
    detection_labels = {
        "rule": build_label_map(
            load_rule_titles(), FIELD_MASKING["rule"].label_prefix or "Detection"
        )
    }
    # 平台級藍隊計分參數（WS3 spec §4.3）。載入一次，不隨每個請求重讀 ——
    # 與 tokens／detection_labels 同一個理由：這份設定在演練期間不該變動。
    blue_scoring_config = BlueScoringConfig.load()
    resolved_action_clock = action_clock or ActionSystemClock()
    # #51：contain 落地後怎麼派送到 Z-MGMT 的 response queue。留給測試換成
    # 假的 dispatcher（不必真的打 HTTP），production 預設用真的
    # `HttpResponseDispatcher`（讀 `RANGE_CORE_RESPONSE_TOKEN`／
    # `PURPLE_RESPONSE_URL` 環境變數）。每個請求建一個新的——`conn` 是
    # 請求級的，dispatcher 不能活得比它的連線久。
    resolved_dispatcher_factory = response_dispatcher_factory or (
        lambda c: HttpResponseDispatcher(conn=c)
    )
    lifecycle_now = exercise_store.now if exercise_store is not None else ActionSystemClock().now
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

        Two gates, in order: a valid token proves the caller is a legitimate
        participant; ``ENDPOINT_MIN_CLEARANCE`` then decides whether *this*
        participant may call *this* endpoint.  Per-player attribution within a
        team stays with ``_source_ip`` -- clearance is a team-level property.
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

        required = ENDPOINT_MIN_CLEARANCE.get((request.method, request.url.path))
        if required is not None and CALLER_CLEARANCE.get(identity, 0) < required:
            raise HTTPException(
                status_code=403, detail="this endpoint requires a higher clearance"
            )
        admission_lifecycle = request.url.path == "/api/exercises/prepare" or (
            request.url.path.startswith("/api/exercises/")
            and "/players/" in request.url.path
        )
        if admission_lifecycle and identity != "admission":
            raise HTTPException(
                status_code=403, detail="this endpoint requires the admission service role"
            )
        if identity == "admission" and not admission_lifecycle:
            raise HTTPException(
                status_code=403,
                detail="the admission service role is limited to lifecycle publication",
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

    def provide_exercise_store(c=Depends(provide_conn)) -> Iterator[ExerciseStore]:
        """Takes `conn` via `Depends`, not a manual call, so FastAPI's
        per-request dependency cache hands back the *same* connection an
        endpoint's own `conn=Depends(provide_conn)` parameter resolves to —
        one request, one transaction. Calling `provide_conn()` directly here
        used to open a second, independent connection per request in
        production, so a concurrent reset between the read and the write
        could target an exercise that no longer existed."""
        if exercise_store is not None:
            yield exercise_store
            return
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
        """Full scenario shape **minus hint text and attack_chain** (#33
        review finding, extended by #126 P0): this is a public,
        unauthenticated listing and Red can reach it directly.

        Hint text: leaving it in makes hint penalties optional — nobody
        needs to call the recorded `POST /api/hints` path when the answer
        is free here. `GET /api/hints?objective_id=` is the sanctioned way
        to see a hint's cost before requesting it; hint *text* is only ever
        returned by `POST /api/hints`, after the usage is recorded.

        `attack_chain`: WS2 spec §4.2 is explicit — "攻擊鏈不給玩家看". It is
        the expected-path denominator for P2 Action Registry, not a walkthrough;
        exposing it here hands Red the answer key that Battleboard deliberately
        anonymizes into `Attack #N` for everyone else."""
        sanitized = []
        for scenario in loaded_catalog.scenarios:
            data = scenario.model_dump()
            data["hints"] = [
                {"objective_id": h["objective_id"], "penalty_percent": h["penalty_percent"]}
                for h in data["hints"]
            ]
            del data["attack_chain"]
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
        prepared = (
            store.preparation(request.exercise_id)
            if request.exercise_id is not None
            else None
        )
        if request.exercise_id is not None and prepared is None:
            raise HTTPException(status_code=409, detail="exercise is not prepared")
        scenario_id = request.scenario_id or (
            prepared.scenario_id if prepared is not None else None
        )
        if scenario_id is None:
            raise HTTPException(
                status_code=422, detail="scenario_id or a prepared exercise_id is required"
            )
        scenario = next(
            (
                candidate
                for candidate in loaded_catalog.scenarios
                if candidate.id == scenario_id
            ),
            None,
        )
        if scenario is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        try:
            if request.exercise_id is not None:
                if request.players:
                    raise HTTPException(
                        status_code=422,
                        detail="players must be registered through Admission before prepared start",
                    )
                return store.start_prepared(request.exercise_id, scenario)
            if not request.players:
                raise HTTPException(status_code=422, detail="players are required")
            return store.start(scenario, request.players)
        except ExerciseAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ExerciseNotPrepared as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post(
        "/api/exercises/prepare",
        response_model=PrepareExercise,
        status_code=201,
    )
    def prepare_exercise(
        request: PrepareExerciseRequest,
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> PrepareExercise:
        scenario = next(
            (candidate for candidate in loaded_catalog.scenarios if candidate.id == request.scenario_id),
            None,
        )
        if scenario is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        try:
            return store.prepare(scenario)
        except ExerciseAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.get(
        "/api/exercises/{exercise_id}/players/{player_id}",
        response_model=AdmissionPlayer,
    )
    def get_admission_player(
        exercise_id: str,
        player_id: str,
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> AdmissionPlayer:
        player = store.player(exercise_id, player_id)
        if player is None:
            raise HTTPException(status_code=404, detail="active player not found")
        return player

    @application.put(
        "/api/exercises/{exercise_id}/players/{player_id}",
        response_model=AdmissionPlayer,
    )
    def register_admission_player(
        exercise_id: str,
        player_id: str,
        request: AdmissionPlayerRegistration,
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> AdmissionPlayer:
        try:
            return store.register_player(exercise_id, player_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ExerciseNotPrepared as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PlayerRegistrationConflict, psycopg.errors.UniqueViolation) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.delete(
        "/api/exercises/{exercise_id}/players/{player_id}",
        status_code=204,
    )
    def revoke_admission_player(
        exercise_id: str,
        player_id: str,
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> None:
        if not store.revoke_player(exercise_id, player_id):
            raise HTTPException(status_code=404, detail="player not found")

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
        if not is_valid_flag_shape(body.flag):
            # Rejects obvious garbage (pasted briefing text, wrong-shaped
            # strings) before the constant-time compare. The shape itself
            # isn't secret -- it's published in the briefing -- so skipping
            # the compare here leaks nothing beyond what the briefing already
            # tells every player.
            return {"accepted": False}
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

    @application.post("/api/blue-actions", status_code=201)
    def submit_blue_action(
        body: BlueActionRequest,
        store: ExerciseStore = Depends(provide_exercise_store),
        conn=Depends(provide_conn),
    ) -> dict:
        """Blue Action ingest (#36 Phase 2, WS3 spec §4).

        No player attribution: the clearance gate on this route
        (`ENDPOINT_MIN_CLEARANCE`) is the only identity check — "who" is
        always the team `blue`, never a person (WS3 spec §5.1).
        """
        exercise, _ = _running_exercise_and_scenario(store)
        action_store = BlueActionStore(conn, clock=resolved_action_clock)
        try:
            recorded = action_store.record(
                # The timestamp is generated inside Range Core, never accepted
                # from the caller.  The injectable clock only exists so the
                # Core Event -> arrival threshold is deterministic in tests.
                exercise.exercise_id, body.action, body.event_id, body.technique
            )
        except UnknownEvent as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except AlreadyJudged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BlueActionRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # #51: Blue Action lands first (the INSERT above already committed),
        # *then* contain gets dispatched -- never the other way around, so a
        # crash between the two leaves a landed-but-undispatched row instead
        # of a phantom dispatch nothing recorded. Dispatch failure is not an
        # exception: it is written back onto the same row so scoring
        # (`StoredDispatchOutcome`) can see it and withhold the point ("must
        # never show points without a block", WS3 spec §5.2's whole point of
        # routing through Range Core instead of a Console double-write).
        dispatch_status: str | None = None
        if recorded.action is BlueActionType.CONTAIN:
            dispatched = resolved_dispatcher_factory(conn).dispatch(
                exercise.exercise_id, recorded.event_id
            )
            dispatch_status = "dispatched" if dispatched else "failed"
            action_store.set_dispatch_status(
                exercise.exercise_id, recorded.event_id, recorded.submitted_at, dispatch_status
            )

        return {
            "team": "blue",
            "action": recorded.action.value,
            "event_id": recorded.event_id,
            "submitted_at": recorded.submitted_at.isoformat(),
            "dispatch_status": dispatch_status,
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
        active_players = {
            row[0]
            for row in conn.execute(
                """SELECT player_id FROM exercise_players
                   WHERE exercise_id = %s AND active""",
                (exercise.exercise_id,),
            ).fetchall()
        }
        completions = tuple(
            completion
            for completion in completions
            if completion.player_id in active_players
        )
        hint_usages = tuple(
            usage for usage in hint_usages if usage.player_id in active_players
        )
        red = derive_scores(scenario, completions, hint_usages).as_dict()

        action_store = BlueActionStore(conn, clock=resolved_action_clock)
        blue = derive_blue_scores(
            action_store.observed_at_by_event(exercise.exercise_id),
            action_store.for_exercise(exercise.exercise_id),
            blue_scoring_config,
            MappingTechniqueTruth(action_store.technique_by_event(exercise.exercise_id)),
            StoredExecutionEvidence(conn, exercise.exercise_id),
            # #51: contain only scores if it was actually dispatched --
            # otherwise a pool-full/network-down failure would still pay out
            # points for a block that never happened.
            dispatch=StoredDispatchOutcome(conn, exercise.exercise_id),
        ).as_dict()
        return {**red, **blue}

    @application.get("/api/events/live")
    def live_events(
        request: Request,
        identity: str = Depends(require_identity),
        store: ExerciseStore = Depends(provide_exercise_store),
    ) -> StreamingResponse:
        """演練事件的 SSE 串流（#36）。

        每則事件的 ``id:`` 是 ``core_events.seq``；瀏覽器斷線重連會把它當
        ``Last-Event-ID`` 送回來，於是續傳就是「seq 大於它的那些」。不帶
        ``Last-Event-ID`` 的新訂閱者從**當下最大 seq 之後**開始收 —— 一連上線
        就把整場歷史灌給他不是即時推送，是回放。

        每個訂閱者有自己的游標與自己的 clearance，彼此不共用狀態；過濾在伺服器
        端發生，不是叫前端自己別顯示。

        **這個端點刻意不吃 ``Depends(provide_conn)``**：那個依賴的連線活到
        「端點函式回傳」為止，而 ``live_events`` 一回傳 ``StreamingResponse``
        函式就結束了 —— 串流實際發生在那之後，連線早被收回。串流是長壽命的，
        需要一條專屬自己、活到 generator 結束才關閉的連線，不能借用請求級的
        那條（借用還會讓這條連線同時被別的請求執行緒使用，psycopg 的連線不是
        執行緒安全的並行對象）。

        **generator 是 async，用 ``await request.is_disconnected()`` 判斷
        客戶端還在不在** —— 這是 ASGI 原生支援的斷線偵測，Starlette 收到客戶端
        關閉連線會正確地把這件事傳達到這裡；換成 sync generator + 輪詢會漏接
        這個訊號，串流永遠不會自己結束。DB 呼叫是同步的 psycopg，直接在 async
        generator 裡呼叫會佔用 event loop 一小段時間 —— 在一場演練事件數以個位
        數計、輪詢間隔 200ms 的規模下可接受，換 asyncpg 之類的非同步驅動是為
        不存在的併發規模付錢。
        """
        exercise, _ = _running_exercise_and_scenario(store)
        clearance = CALLER_CLEARANCE.get(identity, 0)
        exercise_id = exercise.exercise_id
        last_event_id_header = request.headers.get("Last-Event-ID")

        async def generate() -> AsyncIterator[str]:
            # 專屬連線，活到這個 generator 結束才關閉 —— 不借用請求級的
            # `provide_conn`（那條連線在端點函式回傳當下就可能被收回）也不
            # 借用外層的 `store`（那會讓同一條連線被輪詢與其他請求並行使用，
            # psycopg 的連線不是執行緒安全的並行對象）。
            stream_conn = connect()
            try:
                stream = CoreEventStream(stream_conn)
                # **只有標頭整個沒出現**才是全新訂閱者，跳過歷史從當下開始。
                # 標頭出現但值是 "0"（或任何解析不出來的垃圾值）是客戶端
                # *主動*告訴我們一個游標 —— `parse_last_event_id` 把它們都
                # 收斂成 0，語意是「給我 seq > 0 的所有事件」，不是「跳過歷史」。
                # 兩者混為一談會讓「帶著 Last-Event-ID: 0 重連」的客戶端漏收
                # 到斷線前就已經存在、還沒送達的事件。
                cursor = parse_last_event_id(last_event_id_header)
                if last_event_id_header is None:
                    cursor = stream.latest_seq(exercise_id)
                async for frame in _stream_events(
                    request,
                    stream_conn,
                    stream,
                    exercise_id,
                    cursor,
                    clearance,
                    detection_labels,
                    max_stream_seconds,
                    lifecycle_now,
                ):
                    yield frame
            finally:
                stream_conn.close()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return application


def _exercise_still_running(
    conn: psycopg.Connection, exercise_id: str, now: datetime
) -> bool:
    """Observe both explicit state and natural lifecycle expiry.

    The caller supplies the same lifecycle clock used by ``ExerciseStore``;
    this keeps deterministic test clocks valid while ensuring a quiet
    exercise closes its stream at ``ends_at`` without waiting for another API
    request to perform lazy state expiration.
    """
    row = conn.execute(
        "SELECT 1 FROM exercises "
        "WHERE exercise_id = %s AND state = 'running' AND ends_at > %s",
        (exercise_id, now),
    ).fetchone()
    return row is not None


async def _stream_events(
    request: Request,
    conn: psycopg.Connection,
    stream: CoreEventStream,
    exercise_id: str,
    cursor: int,
    clearance: int,
    labels: Mapping[str, Mapping[str, str]],
    max_stream_seconds: float,
    lifecycle_now: Callable[[], datetime],
) -> AsyncIterator[str]:
    idle = 0.0
    deadline = time.monotonic() + max_stream_seconds
    while time.monotonic() < deadline and not await request.is_disconnected():
        if not _exercise_still_running(conn, exercise_id, lifecycle_now()):
            # 演練結束（或已被別場取代）→ 乾淨關閉，不留懸掛連線。
            return

        batch = stream.after(exercise_id, cursor)
        if batch:
            for _, frame in frames_for(batch, clearance, labels):
                yield frame
            # 游標推進到整批最大 seq，不是最後一個推出去的 —— 一批全被
            # 過濾掉時才不會卡在原地反覆重讀。
            cursor = batch[-1].seq
            idle = 0.0
            continue

        idle += POLL_INTERVAL_S
        if idle >= KEEPALIVE_INTERVAL_S:
            idle = 0.0
            yield comment_frame("keep-alive")
        await asyncio.sleep(POLL_INTERVAL_S)


app = create_app()
