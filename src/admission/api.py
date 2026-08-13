from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="src/admission/templates")


# ---- 假資料，照抄 demo.html 裡寫死的內容。之後接上真實 store 層時再替換 ----

FAKE_COUNTS = {"total": 52, "claimed": 41, "ready": 6, "requested": 3, "free": 2}

FAKE_SEATS = {
    "red": [
        {"seat_id": "r-01", "kind": "shell", "state": "claimed", "player_id": "p_4a1c77",
         "endpoints": "10.167.30.11:7681", "claimed_at": "09:12:04"},
        {"seat_id": "r-02", "kind": "shell", "state": "claimed", "player_id": "p_9e2b30",
         "endpoints": "10.167.30.12:7681", "claimed_at": "09:12:41"},
        {"seat_id": "r-05", "kind": "shell", "state": "ready", "player_id": None,
         "endpoints": "10.167.30.15:7681", "claimed_at": None},
        {"seat_id": "r-06", "kind": "shell", "state": "requested", "player_id": None,
         "endpoints": None, "claimed_at": None},
        {"seat_id": "r-08", "kind": "shell", "state": "released", "player_id": None,
         "endpoints": "10.167.30.18:7681", "claimed_at": None},
        {"seat_id": "r-09", "kind": "shell", "state": "free", "player_id": None,
         "endpoints": None, "claimed_at": None},
    ],
    "blue": [
        {"seat_id": "b-01", "kind": "shell", "state": "claimed", "player_id": "p_c03f18",
         "endpoints": "10.167.60.11:7681 (a)", "claimed_at": "09:11:55"},
        {"seat_id": "b-03", "kind": "shell", "state": "ready", "player_id": None,
         "endpoints": "10.167.60.31:7681 (a)", "claimed_at": None},
        {"seat_id": "b-05", "kind": "console", "state": "free", "player_id": None,
         "endpoints": None, "claimed_at": None},
    ],
}

FAKE_QUEUE = [
    {"seat_id": "r-06", "waiting_seconds": 436, "stage": "agent 尚未輪詢到"},
    {"seat_id": "r-07", "waiting_seconds": 443, "stage": "建立容器網路"},
    {"seat_id": "b-04", "waiting_seconds": 455, "stage": "啟動 ttyd，等待回寫 endpoint"},
]

FAKE_POOL_CONFIG = {"red_cap": 30, "blue_cap": 20, "locked_at": None}

FAKE_LINKS = [
    {"link": ".../j/8fK2q9", "status": "used", "seat_id": "r-04"},
    {"link": ".../j/mZ7pL1", "status": "used", "seat_id": "b-02"},
    {"link": ".../j/Qv3nD8", "status": "unused", "seat_id": None},
    {"link": ".../j/xR9tW4", "status": "expired", "seat_id": None},
]

FAKE_LOG = [
    {"time": "09:13:52", "css_class": "ok", "message": "verify(進場碼) → true"},
    {"time": "09:13:52", "css_class": "mint", "message": "claim seat r-04 → 鑄造 player_id=p_15d8ae"},
    {"time": "09:13:31", "css_class": "no", "message": "verify(進場碼) → false（時間窗已過）"},
]


@app.get("/admission/{exercise_id}/console")
def console(request: Request, exercise_id: str, team: str = "red"):
    seats = FAKE_SEATS.get(team, [])
    claimed_seats = [s for s in seats if s["state"] == "claimed"]
    return templates.TemplateResponse(
        request,
        "event_control.html",
        {
            "exercise_id": exercise_id,
            "exercise_status": "running",
            "team": team,
            "counts": FAKE_COUNTS,
            "seats": seats,
            "claimed_seats": claimed_seats,
            "queue": FAKE_QUEUE,
            "pool_config": FAKE_POOL_CONFIG,
            "links": FAKE_LINKS,
            "site_code": "UEX-QQC",
            "log_lines": FAKE_LOG,
        },
    )