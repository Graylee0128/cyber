"""Black-box proof that EDGE routes only Admission-authorized WebSockets."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import socket
import time
from http.cookies import SimpleCookie
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PURPLE_ACCESS_E2E") != "1",
        reason="access-plane compose profile is not running",
    ),
]

ADMISSION_URL = os.environ.get("PURPLE_ADMISSION_URL", "http://localhost:8002")
EDGE_HOST = os.environ.get("PURPLE_EDGE_HOST", "localhost")
EDGE_PORT = int(os.environ.get("PURPLE_EDGE_PORT", "8088"))
INSTRUCTOR_TOKEN = "e2e-service-token"
RANGE_URL = os.environ.get("PURPLE_RANGE_CORE_URL", "http://localhost:8003")
RANGE_TOKEN = "e2e-admission-token"
ONSITE_SECRET = b"e2e-site-secret"


def _json_request(method: str, path: str, payload: dict | None = None, *, token: str | None = None, cookie: str | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    request = Request(ADMISSION_URL + path, data=body, headers=headers, method=method)
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read() or b"{}")
    with response:
        raw = response.read()
        return response.status, dict(response.headers), json.loads(raw) if raw else None


def _onsite_code(exercise_id: str) -> str:
    window = int(time.time()) // 60
    message = f"{exercise_id}:{window}".encode()
    return hmac.new(ONSITE_SECRET, message, hashlib.sha256).hexdigest()[:12]


def _claim(exercise_id: str, team: str) -> tuple[dict, str]:
    status, headers, result = _json_request(
        "POST",
        f"/admission/{exercise_id}/claims",
        {"team": team, "onsite_code": _onsite_code(exercise_id)},
    )
    assert status == 201, result
    parsed = SimpleCookie()
    set_cookie = next(value for key, value in headers.items() if key.lower() == "set-cookie")
    parsed.load(set_cookie)
    return result, f"admission_session={parsed['admission_session'].value}"


def _range_request(method: str, path: str, payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        RANGE_URL + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {RANGE_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        response = urlopen(request, timeout=10)
    except HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def _ready(seat_id: str, endpoints: list[dict]) -> None:
    status, _, result = _json_request(
        "POST",
        f"/admission/seats/{seat_id}/ready",
        {"endpoints": endpoints},
        token=INSTRUCTOR_TOKEN,
    )
    assert status == 204, result


def _recv_exact(sock: socket.socket, size: int, initial: bytes = b"") -> bytes:
    data = initial
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise AssertionError("WebSocket closed before the identity frame arrived")
        data += chunk
    return data


def _websocket_identity(terminal: str, cookie: str, *, forged_upstream: str | None = None) -> tuple[int, str | None]:
    key = base64.b64encode(os.urandom(16)).decode()
    headers = [
        f"GET /terminal/{terminal}/ HTTP/1.1",
        f"Host: {EDGE_HOST}:{EDGE_PORT}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        f"Cookie: {cookie}",
    ]
    if forged_upstream:
        headers.append(f"X-Ttyd-Upstream: {forged_upstream}")

    with socket.create_connection((EDGE_HOST, EDGE_PORT), timeout=10) as sock:
        sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
        received = b""
        while b"\r\n\r\n" not in received:
            received += sock.recv(4096)
        head, frame = received.split(b"\r\n\r\n", 1)
        status = int(head.split(b" ", 2)[1])
        if status != http.client.SWITCHING_PROTOCOLS:
            return status, None

        frame = _recv_exact(sock, 2, frame)
        length = frame[1] & 0x7F
        assert frame[0] == 0x81 and length < 126
        payload = _recv_exact(sock, 2 + length, frame)[2:]
        return status, json.loads(payload)["seat"]


def test_admission_authorizes_only_each_sessions_own_terminal_and_revokes_immediately():
    status, prepared = _range_request(
        "POST", "/api/exercises/prepare", {"scenario_id": "admission-e2e"}
    )
    assert status == 201, prepared
    exercise_id = prepared["exercise_id"]

    status, _, result = _json_request(
        "PUT",
        f"/admission/{exercise_id}/pool-config",
        {"red_cap": 1, "blue_cap": 1},
        token=INSTRUCTOR_TOKEN,
    )
    assert status == 204, result
    red, red_cookie = _claim(exercise_id, "red")
    # Blue seats are prebuilt before this claim; pool lock follows it.
    blue, blue_cookie = _claim(exercise_id, "blue")
    for player in (red, blue):
        status, absent = _range_request(
            "GET", f"/api/exercises/{exercise_id}/players/{player['player_id']}"
        )
        assert status == 404, absent

    status, _, result = _json_request(
        "POST", f"/admission/{exercise_id}/pool-config/lock", token=INSTRUCTOR_TOKEN
    )
    assert status == 204, result
    status, _, full = _json_request(
        "POST",
        f"/admission/{exercise_id}/claims",
        {"team": "blue", "onsite_code": _onsite_code(exercise_id)},
    )
    assert status == 409 and full["detail"]["code"] == "team_full"
    _ready(
        red["seat_id"],
        [{"terminal": "main", "host": "10.167.30.60", "port": 7681}],
    )
    _ready(
        blue["seat_id"],
        [
            {"terminal": "a", "host": "10.167.60.11", "port": 7681},
            {"terminal": "b", "host": "10.167.60.11", "port": 7681},
        ],
    )

    for player in (red, blue):
        status, registered = _range_request(
            "GET", f"/api/exercises/{exercise_id}/players/{player['player_id']}"
        )
        assert status == 200, registered
        assert registered["state"] == "active"

    assert _websocket_identity("main", red_cookie) == (101, "red")
    assert _websocket_identity("a", blue_cookie) == (101, "blue")
    assert _websocket_identity("main", red_cookie) == (101, "red")  # reconnect affinity

    assert _websocket_identity("main", "admission_session=forged", forged_upstream="10.167.30.60:7681")[0] == 403
    assert _websocket_identity("a", red_cookie)[0] == 403
    assert _websocket_identity("main", blue_cookie)[0] == 403

    status, _, result = _json_request(
        "POST",
        f"/admission/seats/{red['seat_id']}/release",
        token=INSTRUCTOR_TOKEN,
    )
    assert status == 204, result
    status, revoked = _range_request(
        "GET", f"/api/exercises/{exercise_id}/players/{red['player_id']}"
    )
    assert status == 404, revoked
    # auth_request runs at each HTTP/WebSocket handshake. Revocation therefore
    # denies this next handshake; it does not force-close an already-upgraded socket.
    assert _websocket_identity("main", red_cookie)[0] == 403
