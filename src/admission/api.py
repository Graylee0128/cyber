from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, model_validator

from admission.credentials import verify_onsite_code
from admission.range_client import HttpRangePublisher
from admission.service import AdmissionService, RangePublisher, TeamFull
from admission.store.db import connect, ensure_schema
from admission.store.instructor_sessions import InstructorSessionStore
from admission.store.pool import PoolConfigStore
from admission.store.seats import SeatStore

SESSION_COOKIE = "admission_session"
#: 教官瀏覽器 session 的 cookie，刻意跟玩家的 `SESSION_COOKIE` 分開命名——
#: 兩者綁的東西不同（座位 vs actor 名字），共用一個 cookie 名字會讓兩套
#: session 生命週期在同一個瀏覽器裡互相踩到。
INSTRUCTOR_SESSION_COOKIE = "admission_instructor_session"
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
    #: 教官瀏覽器 session 的存活時間。跟 `session_ttl_seconds`（玩家座位 session）
    #: 分開設定——語意不同：一整個活動日的教官登入不該因為玩家 session 的
    #: TTL 調整而被連動改變（#126 item 2）。
    instructor_session_ttl_seconds: int | None = None
    #: Admission 自己的公開網址（玩家瀏覽器打得到的那個，不是 Product UI gateway
    #: 內部打的 docker service name）。只用來把一次性邀請連結組成**可點的網址**
    #: （#143 item 3）——沒設時 Event Control 仍照舊只印出裸 token，不是壞掉，
    #: 只是少了那層方便。
    public_base_url: str = ""
    #: Player Portal（Product UI）的公開網址。玩家在 `join.html` 領完位後要導去
    #: 這裡看畫面；`join.html` 是 Admission 自己 serve 的模板，跟 Product UI
    #: 是不同 origin，沒有這個值就無從構造導頁連結（#143 item 3）。
    player_portal_base_url: str = ""
    #: session cookie 要不要帶 `Secure`。預設 `True`，且**不該為了方便而改掉**——
    #: 沒有 `Secure` 的 session cookie 會跟著明文 HTTP 一起走，任何在路徑上的人
    #: 都能抄走教官或玩家的 session。
    #:
    #: 之所以還是做成開關：`Secure` 的 cookie 只有在 secure context 才會被瀏覽器
    #: 存下來，而 `http://<區網 IP>:8090` 不是 secure context（只有 `localhost`
    #: 例外）。也就是說在明文 HTTP 遠端部署下，登入會回 204 但 cookie 直接被丟掉，
    #: `/instructor/`、`/purple/`、`/event-control/` 與領位流程全部**結構性**打不開，
    #: 而且沒有任何錯誤訊號——只有一個裸的 403。deploy.sh 的 demo 部署沒有 TLS，
    #: 因此明確設 `ADMISSION_COOKIE_SECURE=0` 並在完成摘要印警告：把降級變成一個
    #: 看得見的選擇，而不是預設值裡一個沒人注意到的洞。
    cookie_secure: bool = True

    @classmethod
    def from_env(cls) -> "AdmissionSettings":
        raw_timeout = os.environ.get("ADMISSION_REQUEST_TIMEOUT_SECONDS")
        raw_session_ttl = os.environ.get("ADMISSION_SESSION_TTL_SECONDS")
        raw_link_ttl = os.environ.get("ADMISSION_REMOTE_LINK_TTL_SECONDS")
        raw_instructor_session_ttl = os.environ.get("ADMISSION_INSTRUCTOR_SESSION_TTL_SECONDS")
        token = os.environ.get("ADMISSION_INSTRUCTOR_TOKEN", "")
        actor = os.environ.get("ADMISSION_INSTRUCTOR_ACTOR", "")
        request_timeout = int(raw_timeout) if raw_timeout else None
        session_ttl = int(raw_session_ttl) if raw_session_ttl else None
        link_ttl = int(raw_link_ttl) if raw_link_ttl else None
        instructor_session_ttl = (
            int(raw_instructor_session_ttl) if raw_instructor_session_ttl else None
        )
        if request_timeout is not None and request_timeout <= 0:
            raise ValueError("ADMISSION_REQUEST_TIMEOUT_SECONDS must be positive")
        if session_ttl is not None and session_ttl <= 0:
            raise ValueError("ADMISSION_SESSION_TTL_SECONDS must be positive")
        if link_ttl is not None and link_ttl <= 0:
            raise ValueError("ADMISSION_REMOTE_LINK_TTL_SECONDS must be positive")
        if instructor_session_ttl is not None and instructor_session_ttl <= 0:
            raise ValueError("ADMISSION_INSTRUCTOR_SESSION_TTL_SECONDS must be positive")
        return cls(
            onsite_secret=os.environ.get("ADMISSION_ONSITE_SECRET", ""),
            instructor_tokens={token: actor} if token and actor else {},
            request_timeout_seconds=request_timeout,
            range_core_url=os.environ.get("ADMISSION_RANGE_CORE_URL", ""),
            range_core_token=os.environ.get("ADMISSION_RANGE_CORE_TOKEN", ""),
            disable_range_publication=os.environ.get("ADMISSION_DISABLE_RANGE_PUBLICATION") == "1",
            session_ttl_seconds=session_ttl,
            remote_link_ttl_seconds=link_ttl,
            instructor_session_ttl_seconds=instructor_session_ttl,
            public_base_url=os.environ.get("ADMISSION_PUBLIC_BASE_URL", ""),
            player_portal_base_url=os.environ.get("ADMISSION_PLAYER_PORTAL_BASE_URL", ""),
            cookie_secure=os.environ.get("ADMISSION_COOKIE_SECURE", "1") != "0",
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


class PrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=1)


class InstructorLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1)


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
            def prepare(self, scenario_id):
                raise RuntimeError("ADMISSION_RANGE_CORE_URL and ADMISSION_RANGE_CORE_TOKEN are required")
            def current_preparation(self):
                raise RuntimeError("ADMISSION_RANGE_CORE_URL and ADMISSION_RANGE_CORE_TOKEN are required")
            def cancel_preparation(self, exercise_id):
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
            # 這個分支是給「玩家重新整理領位頁」用的 reconnect，不是二次領位——
            # 但它原本只比對 exercise_id，沒比對 team。同一瀏覽器已經是紅隊時，
            # 用一把全新、未消費過的藍隊憑證再送一次，會被靜默接回舊的紅隊座位：
            # 200、看起來像成功、body.remote_token／onsite_code 完全沒被檢查，
            # 教官也看不出那把藍隊連結其實從未被消費（pre-UAT 2026-08-16 撞到，
            # 兩個不同的人共用一台裝置時尤其危險：第二個人會被接回第一個人的
            # 身分與計分）。這裡不裁決「能不能換隊」——那是產品決策——只是把
            # 現況的靜默行為換成看得見的拒絕，fail closed。
            if existing["team"] != body.team:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "team_mismatch",
                        "message": f"this browser already holds a {existing['team']} seat in this exercise",
                        "team": existing["team"],
                    },
                )
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
        response.set_cookie(SESSION_COOKIE, token, httponly=True,
                            secure=configured.cookie_secure, samesite="strict")
        return {k: result[k] for k in ("seat_id", "player_id", "state", "team")}

    @application.post("/admission/logout", status_code=204)
    def logout(request: Request, response: Response,
               svc: AdmissionService = Depends(service)) -> Response:
        if not svc.logout(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="active session required")
        response.delete_cookie(SESSION_COOKIE, secure=configured.cookie_secure,
                               httponly=True, samesite="strict")
        response.status_code = 204
        return response

    @application.post("/admission/instructor/login", status_code=204)
    def instructor_login(
        body: InstructorLoginRequest, response: Response, c=Depends(provide_conn)
    ) -> Response:
        """人在瀏覽器裡登入教官畫面（#126 item 2）。**不是**新密鑰——憑證沿用
        `instructor_tokens`，跟 `instructor()` dependency 驗的是同一把鑰匙，
        只是這裡走表單提交而非 Authorization header，換一個 session cookie
        供 nginx `auth_request` 用。這個端點本身刻意不受 `auth_request` 保護
        （否則登入永遠拿不到能通過它的 cookie），CIDR 仍是它唯一的外圍防線。
        """
        if configured.instructor_session_ttl_seconds is None:
            raise HTTPException(
                status_code=503, detail="ADMISSION_INSTRUCTOR_SESSION_TTL_SECONDS is required"
            )
        actor = next(
            (
                candidate
                for token, candidate in (configured.instructor_tokens or {}).items()
                if secrets.compare_digest(body.token, token)
            ),
            None,
        )
        if actor is None:
            raise HTTPException(status_code=403, detail="invalid instructor credential")
        session_token = InstructorSessionStore(c).bind(
            actor, configured.instructor_session_ttl_seconds
        )
        response.set_cookie(
            INSTRUCTOR_SESSION_COOKIE, session_token,
            httponly=True, secure=configured.cookie_secure, samesite="strict",
        )
        response.status_code = 204
        return response

    @application.post("/admission/instructor/logout", status_code=204)
    def instructor_logout(request: Request, response: Response, c=Depends(provide_conn)) -> Response:
        InstructorSessionStore(c).revoke(request.cookies.get(INSTRUCTOR_SESSION_COOKIE))
        response.delete_cookie(INSTRUCTOR_SESSION_COOKIE, secure=configured.cookie_secure,
                               httponly=True, samesite="strict")
        response.status_code = 204
        return response

    @application.get("/admission/auth/instructor", status_code=204)
    def auth_instructor(request: Request, c=Depends(provide_conn)) -> Response:
        """nginx `auth_request` 的目標端點——跟 `/admission/auth/ttyd/{terminal}`
        同一個角色，差別是驗的不是「這個 session 擁不擁有這台終端機」，是
        「這個瀏覽器有沒有登入教官」。204＝有，403＝沒有；nginx 據此決定要不要
        放行教官／purple／event-control 的靜態頁與 gateway 呼叫（見
        deploy/ui/default.conf.template）。"""
        actor = InstructorSessionStore(c).resolve(request.cookies.get(INSTRUCTOR_SESSION_COOKIE))
        if actor is None:
            raise HTTPException(status_code=403, detail="instructor session required")
        return Response(status_code=204, headers={"X-Instructor-Actor": actor})

    @application.post("/admission/{exercise_id}/remote-links", status_code=201)
    def create_remote_link(exercise_id: str, _actor: str = Depends(instructor),
                           c=Depends(provide_conn)) -> dict:
        if configured.remote_link_ttl_seconds is None:
            raise HTTPException(status_code=503, detail="ADMISSION_REMOTE_LINK_TTL_SECONDS is required")
        link_id, token = SeatStore(c).issue_remote_link(
            exercise_id, configured.remote_link_ttl_seconds
        )
        # `join_url` 只在設了公開網址時才給——沒設就是 None，Event Control 退回
        # 只印裸 token（#143 item 3 之前唯一存在的行為），不是壞掉，只是少一層方便。
        join_url = (
            f"{configured.public_base_url.rstrip('/')}"
            f"/admission/{exercise_id}/join?token={token}"
            if configured.public_base_url else None
        )
        return {"link_id": link_id, "token": token, "join_url": join_url}

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
        response.set_cookie(SESSION_COOKIE, token, httponly=True,
                            secure=configured.cookie_secure, samesite="strict")
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

    @application.get("/admission/auth/seat", status_code=204)
    def auth_seat(request: Request, svc: AdmissionService = Depends(service)) -> Response:
        """Product UI gateway 的 `auth_request` 目標，用於名冊歸屬（#126 item 4）。

        跟 `/admission/auth/ttyd/{terminal}` 同一個角色：回答「這個 session 對
        應到哪台機器」。差別是那條回 ttyd 的 upstream 位址（要拿去 proxy_pass），
        這條回名冊上的來源 IP（要拿去當 Range Core 的 `X-Seat-Source-Ip`）。
        呼叫者是 Product UI 的 gateway 而非 Z-EDGE：代宣告來源 IP 需要同時握有
        Range Core 的服務 token，而 Z-EDGE 必須維持零憑證（WS8 spec §5.3）。

        藍隊 session 也回 204，只是不帶標頭——藍隊不做個人計分，沒有名冊歸屬，
        但仍然是合法的 session，不該被擋在遊戲 API 之外（例如讀 `/api/score`）。
        """
        token = request.cookies.get(SESSION_COOKIE)
        if svc.resolve_session(token) is None:
            raise HTTPException(status_code=403, detail="active seat session required")
        source_ip = svc.seat_source_ip(token)
        headers = {"X-Seat-Source-Ip": source_ip} if source_ip else {}
        return Response(status_code=204, headers=headers)

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

    @application.post("/admission/prepare", status_code=201)
    def prepare(body: PrepareRequest, _actor: str = Depends(instructor),
                svc: AdmissionService = Depends(service)) -> dict:
        """#143 項目 1：教官控台自己打 `/api/exercises/prepare` 必定 403
        （Range Core 的 `require_identity` 只認 `admission` 服務身分）。這裡代打——
        Admission 本來就持有那個身分的 token。exercise_id 由 Range Core 生成
        並原樣回傳，不在這裡另外編號（見 ADR 0003）。"""
        try:
            return svc.prepare(body.scenario_id)
        except HTTPError as exc:
            detail = exc.reason
            try:
                payload = json.loads(exc.read())
                detail = payload.get("detail", detail)
            except (ValueError, AttributeError):
                pass
            raise HTTPException(status_code=exc.code, detail=detail) from exc

    @application.get("/admission/prepared")
    def get_current_preparation(_actor: str = Depends(instructor),
                                svc: AdmissionService = Depends(service)) -> dict:
        """#163：教官忘記複製 `prepare` 回傳的 exercise_id 時，能查回目前是否
        有一筆 `prepared`，不用直接連資料庫。同一把 admission 服務身分反代。"""
        prepared = svc.current_preparation()
        if prepared is None:
            raise HTTPException(status_code=404, detail="no exercise is currently prepared")
        return prepared

    @application.delete("/admission/prepared/{exercise_id}", status_code=204)
    def cancel_preparation(exercise_id: str, _actor: str = Depends(instructor),
                           svc: AdmissionService = Depends(service)) -> Response:
        """#163：讓教官能主動放棄一筆 `prepared`，不用直接連資料庫刪。冪等——
        再取消一次已經被消費（`started`）或不存在的 exercise_id 一律 404，
        不假裝成功。"""
        if not svc.cancel_preparation(exercise_id):
            raise HTTPException(status_code=404, detail="exercise is not prepared")
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
            request, "join.html", {
                "exercise_id": exercise_id,
                "portal_base_url": configured.player_portal_base_url,
                **result,
            }
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
