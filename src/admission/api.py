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

    @classmethod
    def from_env(cls) -> "AdmissionSettings":
        raw_timeout = os.environ.get("ADMISSION_REQUEST_TIMEOUT_SECONDS")
        token = os.environ.get("ADMISSION_INSTRUCTOR_TOKEN", "")
        return cls(
            onsite_secret=os.environ.get("ADMISSION_ONSITE_SECRET", ""),
            instructor_tokens={token: "instructor"} if token else {},
            request_timeout_seconds=int(raw_timeout) if raw_timeout else None,
            range_core_url=os.environ.get("ADMISSION_RANGE_CORE_URL", ""),
            range_core_token=os.environ.get("ADMISSION_RANGE_CORE_TOKEN", ""),
            disable_range_publication=os.environ.get("ADMISSION_DISABLE_RANGE_PUBLICATION") == "1",
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
        return AdmissionService(c, publisher=resolved_publisher, alerter=alerter)

    def instructor(request: Request) -> str:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        for token, actor in (configured.instructor_tokens or {}).items():
            if secrets.compare_digest(supplied, token):
                return actor
        raise HTTPException(status_code=403, detail="instructor service token required")

    @application.post("/admission/{exercise_id}/claims", status_code=201)
    def claim(exercise_id: str, body: ClaimRequest, request: Request, response: Response,
              svc: AdmissionService = Depends(service)) -> dict:
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
        PoolConfigStore(c).set_caps(exercise_id, body.red_cap, body.blue_cap)
        return Response(status_code=204)

    @application.post("/admission/{exercise_id}/pool-config/lock", status_code=204)
    def lock_pool(exercise_id: str, _actor: str = Depends(instructor), c=Depends(provide_conn)) -> Response:
        pools = PoolConfigStore(c)
        cfg = pools.get(exercise_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="pool config not found")
        if cfg.locked_at is None:
            SeatStore(c).bulk_build_blue_seats(exercise_id, cfg.blue_cap)
            pools.lock(exercise_id)
        return Response(status_code=204)

    @application.post("/admission/maintenance/expire", status_code=200)
    def expire(_actor: str = Depends(instructor), svc: AdmissionService = Depends(service)) -> dict:
        if configured.request_timeout_seconds is None:
            raise HTTPException(status_code=503, detail="ADMISSION_REQUEST_TIMEOUT_SECONDS is required")
        return svc.expire_requests(configured.request_timeout_seconds)

    @application.get("/admission/{exercise_id}/console")
    def console(request: Request, exercise_id: str, team: str = "red", c=Depends(provide_conn)):
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
