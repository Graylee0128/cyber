"""封鎖路徑跨容器實證（#51／WS3 spec §5.2）。

單元測試（`tests/range_core/test_response_dispatch.py`、
`tests/response/test_enqueue_endpoint.py`）驗的是**程式邏輯**：dispatcher 對
一個測試行程內的假伺服器發 POST、enqueue 端點對一個直接建構的 handler 收
POST。兩邊都對，整條路仍可能是死的——因為那一跳真正會斷在**部署沒配到
`RANGE_CORE_RESPONSE_TOKEN`／`PURPLE_RESPONSE_URL`**，而那是 compose／deploy
設定，不是 Python 程式碼，任何單元測試都碰不到。

這條測試補的就是那個縫：Range Core（Z-APP 住戶）與 receiver（Z-MGMT 住戶）
是兩個真的容器、跨真的網路，命令要真的出現在 Z-MGMT 的佇列裡。它會因為
「compose 少配一個環境變數」而變紅——這正是它存在的理由。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg
import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PURPLE_ACCESS_E2E") != "1",
        reason="access-plane compose profile is not running",
    ),
]

RANGE_URL = os.environ.get("PURPLE_RANGE_CORE_URL", "http://localhost:8003")
RECEIVER_URL = os.environ.get("PURPLE_RECEIVER_URL", "http://localhost:8004")
PG_DSN = os.environ.get(
    "PURPLE_PG_DSN", "postgresql://admission:admission-e2e@localhost:5433/admission"
)
INSTRUCTOR_TOKEN = "e2e-instructor-token"
BLUE_TOKEN = "e2e-blue-token"
SOURCE_IP = "10.167.30.11"
SERVICE = "vulnerable-app"
TECHNIQUE = "T1190"


def _request(url: str, path: str, method: str, token: str | None, payload: dict | None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url + path, data=body, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=15)
    except HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def _insert_detection_event(exercise_id: str, event_id: str, observed_at: datetime) -> None:
    """把一筆 detection Core Event 直接寫進資料庫。

    走 receiver `/webhook` 得先造一份 Grafana 告警並等 Loki／規則對上，那是
    #26 的鏈路、不是這條測試要證的事。這裡的前提條件只有一個：**有一筆藍隊
    可以 contain 的事件**，所以直接落地最短、也不會把別票的失敗算到這票頭上。
    """
    event = {
        "event_id": event_id,
        "event_type": "attack.detected",
        "lifecycle": "firing",
        "exercise_id": exercise_id,
        "scenario_id": "admission-e2e",
        "severity": "high",
        "technique": TECHNIQUE,
        "observed_at": observed_at.isoformat(),
        "target": {"source_ip": SOURCE_IP, "service": SERVICE},
    }
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO core_events
                (event_id, lifecycle, event_type, exercise_id, scenario_id, observed_at, event)
            VALUES (%s, 'firing', 'attack.detected', %s, 'admission-e2e', %s, %s)
            ON CONFLICT (event_id, lifecycle) DO NOTHING
            """,
            (event_id, exercise_id, observed_at, json.dumps(event)),
        )


def _dispatch_status(exercise_id: str, event_id: str) -> str | None:
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        row = conn.execute(
            "SELECT dispatch_status FROM exercise_blue_actions "
            "WHERE exercise_id = %s AND event_id = %s AND action = 'contain' "
            "ORDER BY submitted_at, id LIMIT 1",
            (exercise_id, event_id),
        ).fetchone()
    return None if row is None else row[0]


def _clear_exercises() -> None:
    """把上一條測試留下的 exercise 清乾淨——**running 與 prepared 都要清**。

    `POST /api/exercises/reset` 只刪 `state = 'running'`（`reset_current()`），
    prepared 的那筆會留著並讓後續 `start` 回 409「an exercise is already
    prepared」。同一個 compose profile 裡的 `test_admission_access.py` 走的正是
    prepare 路徑，且檔名排序在本檔之前——所以只靠 reset 會依測試順序而時綠時紅。
    這是測試載具的清場，不是 production 行為：`exercise_players` 等衍生資料表
    都是 `ON DELETE CASCADE`。
    """
    _request(RANGE_URL, "/api/exercises/reset", "POST", INSTRUCTOR_TOKEN, {})
    with psycopg.connect(PG_DSN, autocommit=True) as conn:
        conn.execute("DELETE FROM exercises WHERE state IN ('running', 'prepared')")


@pytest.fixture
def running_exercise():
    """一場乾淨的 running exercise。"""
    _clear_exercises()
    status, started = _request(
        RANGE_URL,
        "/api/exercises/start",
        "POST",
        INSTRUCTOR_TOKEN,
        {
            "scenario_id": "admission-e2e",
            "players": [{"player_id": f"red-{uuid.uuid4().hex[:8]}", "source_ip": SOURCE_IP}],
        },
    )
    assert status == 201, started
    yield started["exercise_id"]
    _clear_exercises()


def test_contain_crosses_z_app_to_z_mgmt_and_lands_in_the_real_queue(running_exercise):
    """藍隊按下封鎖 → 命令真的進到 Z-MGMT 那個容器的佇列裡。

    這條斷了代表整個封鎖功能是死的，不管單元測試多綠。
    """
    exercise_id = running_exercise
    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    # 觀測時間往前挪幾秒：反應時間要落在 `Contain < 60 sec` 門檻內，才驗得到
    # 「派送成功 → 真的給分」，而不是被門檻擋掉分不出是哪個原因沒給分。
    _insert_detection_event(
        exercise_id, event_id, datetime.now(timezone.utc) - timedelta(seconds=5)
    )

    status, submitted = _request(
        RANGE_URL, "/api/blue-actions", "POST", BLUE_TOKEN,
        {"action": "contain", "event_id": event_id},
    )
    assert status == 201, submitted

    # 1) Range Core 自己說派送成功
    assert submitted["dispatch_status"] == "dispatched", submitted
    # 2) 那個狀態真的回填到 Blue Action 那一列上
    assert _dispatch_status(exercise_id, event_id) == "dispatched"

    # 3) 命令真的在 Z-MGMT 的佇列裡——這一步只有跨容器那一跳真的通了才成立
    status, pulled = _request(RECEIVER_URL, "/response/commands", "GET", None, None)
    assert status == 200, pulled
    commands = {command["event_id"]: command for command in pulled["commands"]}
    assert event_id in commands, pulled
    command = commands[event_id]
    assert command["source_ip"] == SOURCE_IP
    assert command["service"] == SERVICE
    assert command["action"] == "block"
    # 人在迴圈（#48）：經這條路徑進來的一律是 manual，計分才認。
    assert command["triggered_by"] == "manual"

    # 4) 派送成功才給 contain 的分——AC「不得出現有分數沒封鎖」的另一半
    status, score = _request(RANGE_URL, "/api/score", "GET", BLUE_TOKEN, None)
    assert status == 200, score
    scored = next(
        event for event in score["blue"]["events"] if event["event_id"] == event_id
    )
    assert scored["awarded"] > 0, scored


def test_enqueue_endpoint_rejects_a_caller_without_the_response_token():
    """Z-MGMT 的 enqueue 端點不是誰都能打——Range Core 是唯一合法呼叫者。

    單元測試已驗過同一件事，但那是對測試行程內建構的 handler；這裡驗的是
    **真的跑在 Z-MGMT 容器裡的那個行程**確實帶著 token 設定啟動了。沒配到
    token 的部署會回 503 而不是 403，一樣會讓這條變紅。
    """
    payload = {
        "event_id": "evt-forged",
        "source_ip": SOURCE_IP,
        "exercise_id": "whatever",
        "scenario_id": "admission-e2e",
        "severity": "high",
        "technique": TECHNIQUE,
        "service": SERVICE,
    }
    status, body = _request(RECEIVER_URL, "/response/enqueue", "POST", "wrong-token", payload)
    assert status == 403, body
