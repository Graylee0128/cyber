"""Response HTTP pull 管路：receiver 排命令，target agent 主動 GET／POST。"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from purple.harness.schema import assert_core_event
from purple.receiver.server import WebhookHandler
from purple.response.agent import ResponseAgent
from purple.response.direct_block import RecordingBlocker
from purple.response.http_link import HttpLink
from purple.response.queue import InMemoryCommandQueue
from purple.store.events import CoreEventStore
from range_core.exercises import (
    ExerciseStore,
    PlayerRegistration,
    ensure_schema as ensure_exercise_schema,
    truncate_all as truncate_exercises,
)
from range_core.scenarios import Scenario


FIRING = {
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "fingerprint": "fp-http-pull",
            "startsAt": "2026-08-11T05:00:00+00:00",
            "labels": {
                "alertname": "FalcoCommandExec",
                "event_type": "attack.detected",
                "technique": "T1059",
                "team": "red",
                "severity": "high",
                "scenario_id": "falco-exec-01",
                "exercise_id": "ex-001",
                "service": "range-target",
                "source_ip": "10.167.30.11",
            },
            "annotations": {"query": "falco command exec", "threshold": "> 0 / 1m"},
            "values": {"A": 1},
        }
    ],
}

FIXED_NOW = datetime(2026, 8, 11, 5, 0, 12, tzinfo=timezone.utc)


def _post(url: str, body: dict) -> dict:
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - localhost test server
        return json.load(response)


def test_webhook_to_agent_pull_to_response_core_event(pg_connection):
    # server.py 的 /webhook 現在會查真正在跑的 exercise（不信 alert 自帶的
    # exercise_id label）；這是唯一打真 HTTP server 的測試，所以只有這裡需要
    # 先在 DB 種一個 running 的 exercise，其餘測試都直接把 exercise_id 傳給
    # ingest_alert/build_core_event，繞過這個查詢。
    ensure_exercise_schema(pg_connection)
    truncate_exercises(pg_connection)
    ExerciseStore(pg_connection).start(
        Scenario.model_validate(
            {
                "id": "falco-exec-01",
                "name": "Command Exec",
                "difficulty": "easy",
                "duration": "30m",
                "objectives": [
                    {"id": "capture_flag", "evaluation": "submission", "points": 500}
                ],
                "targets": [{"host": "target-01", "surfaces": ["shell"]}],
                "expected_sources": ["falco"],
                "attack_chain": [
                    {
                        "id": "execute-command",
                        "technique": "T1059",
                        "description": "Execute a target command.",
                    }
                ],
                "reset_scope": "exercise",
            }
        ),
        (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),),
    )

    command_queue = InMemoryCommandQueue()

    class Handler(WebhookHandler):
        # 這條測試驗的是整條鏈（webhook → queue → agent pull → response event），
        # 屬於測試載具用途，不是演練計分路徑，所以明確開啟 auto_response（票 48）。
        response_queue = command_queue
        auto_response = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    events = CoreEventStore(pg_connection)
    mark = events.now()
    try:
        assert _post(f"{base_url}/webhook", FIRING)["emitted"]

        # HttpLink 只有 agent 主動發起的 pull/report，沒有 listen/accept。
        link = HttpLink(base_url)
        assert not hasattr(link, "listen")
        assert not hasattr(link, "accept")

        [response_event] = ResponseAgent(
            link=link,
            blocker=RecordingBlocker(),
            now=lambda: FIXED_NOW,
        ).run_once()

        assert_core_event(response_event)
        assert response_event["event_type"] == "response.executed"
        assert response_event["target"]["source_ip"] == "10.167.30.11"

        stored = events.since(mark)
        assert [event["event_type"] for event in stored] == [
            "attack.detected",
            "response.executed",
        ]
        assert stored[1] == response_event
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
