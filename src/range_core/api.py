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
from collections.abc import AsyncIterator, Iterator, Mapping
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

from range_core.event_stream import (
    CoreEventStream,
    comment_frame,
    frames_for,
    parse_last_event_id,
)

from range_core.exercises import (
    Exercise,
    ExerciseAlreadyRunning,
    ExerciseStore,
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

ENDPOINT_MIN_CLEARANCE: dict[tuple[str, str], int] = {
    ("POST", "/api/exercises/start"): CALLER_CLEARANCE["instructor"],
    ("POST", "/api/exercises/reset"): CALLER_CLEARANCE["instructor"],
}


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

    Deployment constraint this implies: Range Core must sit where Kali hosts
    connect to it directly. Any reverse proxy or NAT in front of it replaces
    every `request.client.host` with the proxy's own address, taking every
    submission/hint/roster lookup to 403 at once (deployment topology is not
    yet decided as of #33 -- WS6/#44 territory, flagged here so it can't be
    discovered by surprise).
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
    # #49 的 rule 匿名標籤。**與 Evidence API 共用同一份排序來源**，否則同一條
    # 規則在 SSE 與 Evidence 兩個畫面會是不同號碼，藍隊會以為那是兩條規則。
    detection_labels = {
        "rule": build_label_map(
            load_rule_titles(), FIELD_MASKING["rule"].label_prefix or "Detection"
        )
    }
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
                cursor = parse_last_event_id(last_event_id_header)
                if cursor == 0:
                    cursor = stream.latest_seq(exercise_id)
                async for frame in _stream_events(
                    request, stream_conn, stream, exercise_id, cursor, clearance, detection_labels
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


def _exercise_still_running(conn: psycopg.Connection, exercise_id: str) -> bool:
    """單純觀察 `exercises` 表的 state，**不觸發到期判定**。

    到期判定（`ExerciseStore._expire_due`）需要一個「現在」，而測試會注入
    `FixedClock` 讓 `ends_at` 落在測試自訂的時間軸上。串流若自己另建一個
    `ExerciseStore` 來查 `current()`，用的會是預設的 `SystemClock`（真實
    wall-clock）—— 對著一個用假時鐘算出來的 `ends_at` 比對真實時間，會把
    測試中「還在跑」的演練誤判成早已過期。到期判定交給其他有正確時鐘設定的
    路徑（`/api/exercises/current`、`/api/score` 等）；串流只負責讀當下狀態。
    """
    row = conn.execute(
        "SELECT 1 FROM exercises WHERE exercise_id = %s AND state = 'running'",
        (exercise_id,),
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
) -> AsyncIterator[str]:
    idle = 0.0
    while not await request.is_disconnected():
        if not _exercise_still_running(conn, exercise_id):
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
