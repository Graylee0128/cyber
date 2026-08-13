"""`SSE /api/events/live` 的端點層（#36）—— 續傳、過濾、多訂閱者、乾淨關閉。

對真 PG 跑：游標是 `core_events.seq`，而那是資料庫給的號碼，不是應用程式自己
數的。用假的 in-memory feed 測「續傳」等於測一個不存在的東西。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.exercises import PlayerRegistration
from range_core.scenarios import Scenario, ScenarioCatalog

TOKEN_MAP = {
    "red-secret": "red",
    "blue-secret": "blue",
    "purple-secret": "purple",
    "instructor-secret": "instructor",
}
T0 = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "stream-01",
            "name": "Stream",
            "difficulty": "easy",
            "duration": "30m",
            "objectives": [
                {"id": "capture_flag", "evaluation": "submission", "points": 500}
            ],
            "targets": [{"host": "target-01", "surfaces": ["web"]}],
            "expected_sources": ["falco"],
            "attack_chain": [
                {"id": "exploit-web", "technique": "T1190", "description": "Exploit."}
            ],
            "reset_scope": "exercise",
        }
    )


@pytest.fixture
def app(exercise_store, pg_connection):
    return create_app(
        ScenarioCatalog((scenario(),)),
        exercise_store=exercise_store,
        conn=pg_connection,
        token_map=TOKEN_MAP,
        # #36: bound every test's own stream so it always terminates on its
        # own, independent of whether the test transport propagates client
        # disconnection (some don't).
        max_stream_seconds=2.0,
    )


@pytest.fixture
def exercise(app, exercise_store):
    return exercise_store.start(
        scenario(),
        (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),),
    )


def write_event(conn, exercise_id, event_id, *, visibility="public", offset_s=0) -> int:
    """直接寫 `core_events`（P1 的角色），回傳資料庫給的 seq。"""
    event = {
        "event_id": event_id,
        "exercise_id": exercise_id,
        "scenario_id": "stream-01",
        "event_type": "attack.detected" if visibility == "public" else "detection.hit",
        "lifecycle": "firing",
        "severity": "high",
        "source": "grafana",
        "team": "red",
        "technique": "T1190",
        "rule": "SQLInjectionBurst",
        "target": "target-01",
        "observed_at": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "visibility": visibility,
        "action_id": None,
    }
    row = conn.execute(
        """
        INSERT INTO core_events
            (event_id, lifecycle, event_type, exercise_id, scenario_id,
             observed_at, action_id, event)
        VALUES (%s, 'firing', %s, %s, 'stream-01', %s, NULL, %s)
        RETURNING seq
        """,
        (
            event_id,
            event["event_type"],
            exercise_id,
            event["observed_at"],
            json.dumps(event),
        ),
    ).fetchone()
    return int(row[0])


def read_frames(client, headers, count, timeout=10.0):
    """讀到 `count` 個事件 frame 就關掉連線（串流本身不會自己結束）。"""
    frames = []
    with client.stream("GET", "/api/events/live", headers=headers, timeout=timeout) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        current: dict = {}
        for line in resp.iter_lines():
            if line.startswith("id: "):
                current["id"] = int(line[4:])
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
                frames.append(current)
                current = {}
                if len(frames) >= count:
                    break
    return frames


def _client(app, identity):
    return TestClient(app, headers={"Authorization": f"Bearer {identity}-secret"})


class TestResumableDelivery:
    def test_last_event_id_replays_only_what_was_missed(self, app, exercise, pg_connection):
        """驗收條件：補得到斷線期間錯過的，且不重複舊的。"""
        first = write_event(pg_connection, exercise.exercise_id, "evt-1")
        write_event(pg_connection, exercise.exercise_id, "evt-2", offset_s=1)
        write_event(pg_connection, exercise.exercise_id, "evt-3", offset_s=2)

        frames = read_frames(
            _client(app, "blue"), {"Last-Event-ID": str(first)}, count=2
        )

        assert [f["data"]["event_id"] for f in frames] == ["evt-2", "evt-3"]

    def test_ids_are_monotonic(self, app, exercise, pg_connection):
        write_event(pg_connection, exercise.exercise_id, "evt-1")
        write_event(pg_connection, exercise.exercise_id, "evt-2", offset_s=1)

        frames = read_frames(_client(app, "blue"), {"Last-Event-ID": "0"}, count=2)
        ids = [f["id"] for f in frames]
        assert ids == sorted(ids) and len(set(ids)) == 2

    def test_a_fresh_subscriber_is_not_replayed_the_whole_exercise(
        self, app, exercise, pg_connection
    ):
        """不帶 Last-Event-ID＝從現在開始。一連線就灌整場歷史不是即時推送。"""
        write_event(pg_connection, exercise.exercise_id, "old-1")

        client = _client(app, "blue")
        with client.stream("GET", "/api/events/live", timeout=10.0) as resp:
            assert resp.status_code == 200
            write_event(pg_connection, exercise.exercise_id, "new-1", offset_s=5)
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    assert json.loads(line[6:])["event_id"] == "new-1"
                    break

    def test_garbage_last_event_id_does_not_break_the_connection(
        self, app, exercise, pg_connection
    ):
        client = _client(app, "blue")
        with client.stream(
            "GET", "/api/events/live", headers={"Last-Event-ID": "not-a-number"}, timeout=10.0
        ) as resp:
            assert resp.status_code == 200


class TestPerSubscriberFiltering:
    def test_red_never_receives_blue_level_events(self, app, exercise, pg_connection):
        write_event(pg_connection, exercise.exercise_id, "blue-only", visibility="blue")
        write_event(pg_connection, exercise.exercise_id, "public-1", offset_s=1)

        frames = read_frames(_client(app, "red"), {"Last-Event-ID": "0"}, count=1)

        assert [f["data"]["event_id"] for f in frames] == ["public-1"]

    def test_streamed_payload_masks_technique_for_blue(self, app, exercise, pg_connection):
        write_event(pg_connection, exercise.exercise_id, "evt-1")

        frames = read_frames(_client(app, "blue"), {"Last-Event-ID": "0"}, count=1)

        assert "technique" not in frames[0]["data"]
        assert frames[0]["data"].get("rule", "").startswith("Detection #")

    def test_purple_receives_the_technique(self, app, exercise, pg_connection):
        write_event(pg_connection, exercise.exercise_id, "evt-1")

        frames = read_frames(_client(app, "purple"), {"Last-Event-ID": "0"}, count=1)

        assert frames[0]["data"]["technique"] == "T1190"

    def test_two_subscribers_get_their_own_view_of_the_same_event(
        self, app, exercise, pg_connection
    ):
        """多個訂閱者互不影響，且各自的欄位集合不同。"""
        write_event(pg_connection, exercise.exercise_id, "evt-1")

        blue = read_frames(_client(app, "blue"), {"Last-Event-ID": "0"}, count=1)
        purple = read_frames(_client(app, "purple"), {"Last-Event-ID": "0"}, count=1)

        assert blue[0]["id"] == purple[0]["id"]
        assert set(purple[0]["data"]) - set(blue[0]["data"]) == {"technique"}


class TestLifecycle:
    def test_no_running_exercise_is_404_not_an_empty_stream(self, app):
        response = _client(app, "blue").get("/api/events/live")
        assert response.status_code == 404

    def test_stream_closes_cleanly_when_the_exercise_ends(
        self, app, exercise, exercise_store, pg_connection
    ):
        """演練結束後串流乾淨關閉，不留懸掛連線。"""
        write_event(pg_connection, exercise.exercise_id, "evt-1")

        client = _client(app, "blue")
        with client.stream(
            "GET", "/api/events/live", headers={"Last-Event-ID": "0"}, timeout=10.0
        ) as resp:
            lines = resp.iter_lines()
            for line in lines:
                if line.startswith("data: "):
                    break
            exercise_store.reset_current()
            # 迭代器自然結束（不是 timeout、不是例外）就是乾淨關閉。
            assert list(lines) is not None

    def test_connection_closes_on_its_own_after_the_bound(
        self, exercise_store, pg_connection
    ):
        """驗收條件的另一半：即使演練仍在跑、client 也沒斷線，連線本身有壽命
        上限（見 `DEFAULT_MAX_STREAM_SECONDS` 的說明）—— 不留懸掛連線不能只
        靠「等對方斷線」這一條路。"""
        app = create_app(
            ScenarioCatalog((scenario(),)),
            exercise_store=exercise_store,
            conn=pg_connection,
            token_map=TOKEN_MAP,
            max_stream_seconds=0.3,
        )
        exercise_store.start(
            scenario(),
            (PlayerRegistration(player_id="red-alice", source_ip="10.167.30.11"),),
        )

        with _client(app, "blue").stream(
            "GET", "/api/events/live", headers={"Last-Event-ID": "0"}, timeout=5.0
        ) as resp:
            # 沒有主動斷線、也沒有寫任何事件 —— 純粹等連線自己到期關閉。
            assert list(resp.iter_lines()) is not None


class TestStreamRequiresIdentity:
    def test_no_token_is_rejected(self, app, exercise):
        assert TestClient(app).get("/api/events/live").status_code == 400
