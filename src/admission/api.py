from __future__ import annotations

import os
import secrets
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, model_validator

from admission.credentials import verify_onsite_code
from admission.range_client import HttpRangePublisher
from admission.service import AdmissionService, RangePublisher, TeamFull
from admission.store.db import connect, ensure_schema
from admission.store.pool import PoolConfigStore
from admission.store.seats import SeatStore

SESSION_COOKIE = "admission_session"
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@dataclass(frozen=True)
class AdmissionSettings:
    onsite_secret: str = ""
    instructor_tokens: Mapping[str, str] | None = None
    request_timeout_seconds: int | None = None
    range_core_url: str = ""
    range_core_token: str = ""
    disable_range_publication: bool = False
    session_ttl_seconds: int | None = None
    remote_link_ttl_seconds: int | None = None

    @classmethod
    def from_env(cls) -> "AdmissionSettings":
        raw_timeout = os.environ.get("ADMISSION_REQUEST_TIMEOUT_SECONDS")
        raw_session_ttl = os.environ.get("ADMISSION_SESSION_TTL_SECONDS")
        raw_link_ttl = os.environ.get("ADMISSION_REMOTE_LINK_TTL_SECONDS")
        token = os.environ.get("ADMISSION_INSTRUCTOR_TOKEN", "")
        actor = os.environ.get("ADMISSION_INSTRUCTOR_ACTOR", "")
        request_timeout = int(raw_timeout) if raw_timeout else None
        session_ttl = int(raw_session_ttl) if raw_session_ttl else None
        link_ttl = int(raw_link_ttl) if raw_link_ttl else None
        if request_timeout is not None and request_timeout <= 0:
            raise ValueError("ADMISSION_REQUEST_TIMEOUT_SECONDS must be positive")
        if session_ttl is not None and session_ttl <= 0:
            raise ValueError("ADMISSION_SESSION_TTL_SECONDS must be positive")
        if link_ttl is not None and link_ttl <= 0:
            raise ValueError("ADMISSION_REMOTE_LINK_TTL_SECONDS must be positive")
        return cls(
            onsite_secret=os.environ.get("ADMISSION_ONSITE_SECRET", ""),
            instructor_tokens={token: actor} if token and actor else {},
            request_timeout_seconds=request_timeout,
            range_core_url=os.environ.get("ADMISSION_RANGE_CORE_URL", ""),
            range_core_token=os.environ.get("ADMISSION_RANGE_CORE_TOKEN", ""),
            disable_range_publication=os.environ.get("ADMISSION_DISABLE_RANGE_PUBLICATION") == "1",
            session_ttl_seconds=session_ttl,
            remote_link_ttl_seconds=link_ttl,
        )


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    team: str
    onsite_code: str | None = None
    remote_token: str | None = None

    @model_validator(mode="after")
    def exactly_one_credential(self):
        if (self.onsite_code is None) == (self.remote_token is None):
            raise ValueError("provide exactly one credential")
        if self.team not in ("red", "blue"):
            raise ValueError("team must be red or blue")
        return self


class EndpointsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoints: list[dict]


class PoolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    red_cap: int = Field(ge=0)
    blue_cap: int = Field(ge=0)


def create_app(
    *, conn=None, publisher: RangePublisher | None = None,
    alerter=None, settings: AdmissionSettings | None = None,
) -> FastAPI:
    configured = settings or AdmissionSettings.from_env()
    resolved_publisher = publisher
    if resolved_publisher is None and configured.range_core_url and configured.range_core_token:
        resolved_publisher = HttpRangePublisher(configured.range_core_url, configured.range_core_token)
    if resolved_publisher is None and not configured.disable_range_publication:
        class MissingRangePublisher:
            def publish_player(self, **_player):
                raise RuntimeError("ADMISSION_RANGE_CORE_URL and ADMISSION_RANGE_CORE_TOKEN are required")
            def revoke_player(self, exercise_id, player_id):
                raise RuntimeError("ADMISSION_RANGE_CORE_URL and ADMISSION_RANGE_CORE_TOKEN are required")
        resolved_publisher = MissingRangePublisher()
    application = FastAPI(title="Admission Service")

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

    def service(c=Depends(provide_conn)) -> AdmissionService:
        return AdmissionService(
            c, publisher=resolved_publisher, alerter=alerter,
            session_ttl_seconds=configured.session_ttl_seconds,
        )

    def instructor(request: Request) -> str:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        for token, actor in (configured.instructor_tokens or {}).items():
            if secrets.compare_digest(supplied, token):
                return actor
        raise HTTPException(
            status_code=401, detail="instructor service token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @application.post("/admission/{exercise_id}/claims", status_code=201)
    def claim(exercise_id: str, body: ClaimRequest, request: Request, response: Response,
              svc: AdmissionService = Depends(service)) -> dict:
        if configured.session_ttl_seconds is None:
            raise HTTPException(status_code=503, detail="ADMISSION_SESSION_TTL_SECONDS is required")
        existing = svc.resolve_session(request.cookies.get(SESSION_COOKIE))
        if existing is not None and existing["exercise_id"] == exercise_id:
            response.status_code = 200
            return {k: existing[k] for k in ("seat_id", "player_id", "state", "team")}
        if body.onsite_code is not None:
            valid = verify_onsite_code(body.onsite_code, configured.onsite_secret, exercise_id)
            if not valid:
                raise HTTPException(status_code=403, detail="invalid or expired credential")
            allocate = lambda: svc.allocate(exercise_id, body.team)
        else:
            allocate = lambda: svc.claim_with_remote_token(exercise_id, body.team, body.remote_token or "")
        try:
            result = allocate()
        except TeamFull as exc:
            raise HTTPException(status_code=409, detail={"code": "team_full", "team": exc.team, "waitlist": False}) from exc
        if result is None:
            raise HTTPException(status_code=403, detail="invalid or used remote credential")
        token = svc.bind_session(result["seat_id"])
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=True, samesite="strict")
        return {k: result[k] for k in ("seat_id", "player_id", "state", "team")}

    @application.post("/admission/logout", status_code=204)
    def logout(request: Request, response: Response,
               svc: AdmissionService = Depends(service)) -> Response:
        if not svc.logout(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="active session required")
        response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="strict")
        response.status_code = 204
        return response

    @application.post("/admission/{exercise_id}/remote-links", status_code=201)
    def create_remote_link(exercise_id: str, _actor: str = Depends(instructor),
                           c=Depends(provide_conn)) -> dict:
        if configured.remote_link_ttl_seconds is None:
            raise HTTPException(status_code=503, detail="ADMISSION_REMOTE_LINK_TTL_SECONDS is required")
        link_id, token = SeatStore(c).issue_remote_link(
            exercise_id, configured.remote_link_ttl_seconds
        )
        return {"link_id": link_id, "token": token}

    @application.delete("/admission/remote-links/{link_id}", status_code=204)
    def revoke_remote_link(link_id: str, actor: str = Depends(instructor),
                           c=Depends(provide_conn)) -> Response:
        if not SeatStore(c).revoke_remote_link(link_id, actor):
            raise HTTPException(status_code=404, detail="active remote link not found")
        return Response(status_code=204)

    @application.get("/admission/seats/pending")
    def pending(team: str | None = None, _actor: str = Depends(instructor),
                c=Depends(provide_conn)) -> list[dict]:
        # #62：host 側 Seat Provisioner Agent 輪詢用（WS8 spec §4.1，pull 模式——
        # 中控／admission 不主動去叫 provisioner 建容器，provisioner 自己來問）。
        return SeatStore(c).list_requested(team)

    @application.get("/admission/seats/active")
    def active(team: str | None = None, _actor: str = Depends(instructor),
               c=Depends(provide_conn)) -> list[str]:
        # #62：provisioner 孤兒回收用——跟 pending 不同，這裡包含 ready／claimed。
        # 拆容器前必須確認座位「已經不算數」（released／failed），不能只看
        # 它是不是還在 requested，否則會把剛建好、正常在用的座位一起拆掉。
        return SeatStore(c).list_active(team)

    @application.post("/admission/seats/{seat_id}/ready", status_code=204)
    def ready(seat_id: str, body: EndpointsRequest, _actor: str = Depends(instructor),
              svc: AdmissionService = Depends(service)) -> Response:
        try:
            found = svc.ready(seat_id, body.endpoints)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail="seat is not awaiting readiness")
        return Response(status_code=204)

    @application.post("/admission/seats/{seat_id}/rebind", status_code=204)
    def rebind(seat_id: str, response: Response, actor: str = Depends(instructor),
               svc: AdmissionService = Depends(service)) -> Response:
        if configured.session_ttl_seconds is None:
            raise HTTPException(status_code=503, detail="ADMISSION_SESSION_TTL_SECONDS is required")
        try:
            token = svc.rebind(seat_id, actor=actor)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="active seat not found") from exc
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=True, samesite="strict")
        response.status_code = 204
        return response

    @application.post("/admission/seats/{seat_id}/release", status_code=204)
    def release(seat_id: str, actor: str = Depends(instructor),
                svc: AdmissionService = Depends(service)) -> Response:
        if not svc.release(seat_id, actor=actor):
            raise HTTPException(status_code=404, detail="seat not found")
        return Response(status_code=204)

    @application.get("/admission/auth/ttyd/{terminal}", status_code=204)
    def auth_ttyd(terminal: str, request: Request, svc: AdmissionService = Depends(service)) -> Response:
        upstream = svc.authorize(request.cookies.get(SESSION_COOKIE), terminal)
        if upstream is None:
            raise HTTPException(status_code=403, detail="session does not own this terminal")
        return Response(status_code=204, headers={"X-Ttyd-Upstream": upstream})

    @application.put("/admission/{exercise_id}/pool-config", status_code=204)
    def pool_config(exercise_id: str, body: PoolRequest, _actor: str = Depends(instructor), c=Depends(provide_conn)) -> Response:
        try:
            PoolConfigStore(c).set_caps_and_prepare_blue(
                exercise_id, body.red_cap, body.blue_cap
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(status_code=204)

    @application.post("/admission/{exercise_id}/pool-config/lock", status_code=204)
    def lock_pool(exercise_id: str, _actor: str = Depends(instructor), c=Depends(provide_conn)) -> Response:
        pools = PoolConfigStore(c)
        try:
            pools.lock_and_build_blue(exercise_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="pool config not found")
        return Response(status_code=204)

    @application.post("/admission/maintenance/expire", status_code=200)
    def expire(_actor: str = Depends(instructor), svc: AdmissionService = Depends(service)) -> dict:
        if configured.request_timeout_seconds is None:
            raise HTTPException(status_code=503, detail="ADMISSION_REQUEST_TIMEOUT_SECONDS is required")
        return svc.expire_requests(configured.request_timeout_seconds)

    @application.get("/admission/alerts")
    def alerts(_actor: str = Depends(instructor), c=Depends(provide_conn)) -> list[dict]:
        rows = c.execute(
            "SELECT seat_id,reason FROM admission_alert ORDER BY alert_id"
        ).fetchall()
        return [{"seat_id": seat_id, "reason": reason} for seat_id, reason in rows]

    @application.get("/admission/{exercise_id}/availability")
    def availability(exercise_id: str, c=Depends(provide_conn)) -> dict:
        result = PoolConfigStore(c).availability(exercise_id)
        if result is None:
            raise HTTPException(status_code=404, detail="pool config not found")
        return result

    @application.get("/admission/{exercise_id}/join")
    def join(request: Request, exercise_id: str, c=Depends(provide_conn)):
        result = PoolConfigStore(c).availability(exercise_id)
        if result is None:
            raise HTTPException(status_code=404, detail="pool config not found")
        return templates.TemplateResponse(
            request, "join.html", {"exercise_id": exercise_id, **result}
        )

    @application.get("/admission/instructor/{exercise_id}/console")
    def console(request: Request, exercise_id: str, team: str = "red",
                _actor: str = Depends(instructor), c=Depends(provide_conn)):
        rows = c.execute(
            """SELECT seat_id,kind,state,player_id,endpoints,claimed_at FROM seat
               WHERE exercise_id=%s AND team=%s ORDER BY seat_id""", (exercise_id, team)
        ).fetchall()
        seats = [dict(zip(("seat_id","kind","state","player_id","endpoints","claimed_at"), row)) for row in rows]
        counts = {"total": len(seats), **SeatStore(c).pool_snapshot(exercise_id, team)}
        cfg = PoolConfigStore(c).get(exercise_id)
        return templates.TemplateResponse(request, "event_control.html", {
            "exercise_id": exercise_id, "exercise_status": "running", "team": team,
            "counts": counts, "seats": seats, "claimed_seats": [s for s in seats if s["state"] == "claimed"],
            "queue": [], "pool_config": cfg or {"red_cap": 0, "blue_cap": 0, "locked_at": None},
            "links": [], "site_code": "rotating", "log_lines": [],
        })

    return application


app = create_app()
