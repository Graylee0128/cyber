"""#153 Experience Layer — Instructor-as-Game-Master HTTP surface
(`POST /api/campaign/{phase,announcement,bgm,pause,resume}`).

Clearance (instructor-only, red/blue get 403) is already covered in
`test_api_clearance.py`; this file is about behavior once an instructor
token is calling. Exercises are started directly through `exercise_store`
(bypassing HTTP), same shortcut `test_campaign_store.py` uses -- the
lifecycle endpoints themselves are covered elsewhere.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from range_core.api import create_app
from range_core.exercises import ExerciseStore
from range_core.flags import FixtureFlagSource
from range_core.scenarios import Scenario, ScenarioCatalog

from tests.range_core.test_exercises import roster, scenario

INSTRUCTOR_AUTH = {"Authorization": "Bearer instructor-secret"}
TOKEN_MAP = {"instructor-secret": "instructor", "red-secret": "red"}


@pytest.fixture
def running_exercise_id(exercise_store: ExerciseStore) -> str:
    return exercise_store.start(scenario(), roster()).exercise_id


@pytest.fixture
def api(exercise_store: ExerciseStore, pg_connection, exercise_clock) -> TestClient:
    app = create_app(
        ScenarioCatalog((scenario(),)),
        exercise_store=exercise_store,
        conn=pg_connection,
        token_map=TOKEN_MAP,
        campaign_clock=exercise_clock,
    )
    return TestClient(app, headers=INSTRUCTOR_AUTH)


class TestPhaseTransition:
    def test_advance_updates_state_and_emits_an_event(
        self, api: TestClient, running_exercise_id: str, pg_connection
    ) -> None:
        response = api.post(
            "/api/campaign/phase",
            json={"phase": "initial", "chapter": "CH1", "label": "Initial Access Detected"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["exercise_id"] == running_exercise_id
        assert body["chapter"] == "CH1"
        assert body["phase"] == "initial"
        assert body["event_id"] is not None
        assert body["seq"] is not None

        row = pg_connection.execute(
            "SELECT event_type, event FROM core_events WHERE event_id = %s",
            (body["event_id"],),
        ).fetchone()
        assert row[0] == "campaign.phase_transition"
        assert row[1]["visibility"] == "public"
        assert row[1]["target"]["chapter"] == "CH1"
        assert row[1]["target"]["label"] == "Initial Access Detected"

    def test_omitted_chapter_keeps_the_previous_one(
        self, api: TestClient, running_exercise_id: str
    ) -> None:
        api.post("/api/campaign/phase", json={"phase": "initial", "chapter": "CH1", "label": "x"})

        response = api.post("/api/campaign/phase", json={"phase": "escalation", "label": "y"})

        assert response.json()["chapter"] == "CH1"

    def test_unknown_phase_value_is_422(self, api: TestClient, running_exercise_id: str) -> None:
        response = api.post(
            "/api/campaign/phase", json={"phase": "not-a-real-phase", "label": "x"}
        )
        assert response.status_code == 422

    def test_without_a_running_exercise_is_404(
        self, exercise_store: ExerciseStore, pg_connection, exercise_clock
    ) -> None:
        app = create_app(
            ScenarioCatalog((scenario(),)),
            exercise_store=exercise_store, conn=pg_connection,
            token_map=TOKEN_MAP, campaign_clock=exercise_clock,
        )
        response = TestClient(app, headers=INSTRUCTOR_AUTH).post(
            "/api/campaign/phase", json={"phase": "initial", "label": "x"}
        )
        assert response.status_code == 404


class TestAnnouncement:
    def test_posts_a_public_core_event(
        self, api: TestClient, running_exercise_id: str, pg_connection
    ) -> None:
        response = api.post(
            "/api/campaign/announcement",
            json={"text": "有人在正式環境開趴", "severity": "warning"},
        )

        assert response.status_code == 201
        row = pg_connection.execute(
            "SELECT event_type, event FROM core_events WHERE event_id = %s",
            (response.json()["event_id"],),
        ).fetchone()
        assert row[0] == "campaign.announcement"
        assert row[1]["visibility"] == "public"
        assert row[1]["target"]["text"] == "有人在正式環境開趴"
        assert row[1]["severity"] == "warning"

    def test_severity_defaults_to_info(self, api: TestClient, running_exercise_id: str) -> None:
        response = api.post("/api/campaign/announcement", json={"text": "hello"})
        assert response.status_code == 201

    def test_text_over_280_chars_is_422(self, api: TestClient, running_exercise_id: str) -> None:
        response = api.post("/api/campaign/announcement", json={"text": "x" * 281})
        assert response.status_code == 422


class TestBgm:
    def test_set_bgm_updates_state_and_emits_no_core_event(
        self, api: TestClient, running_exercise_id: str, pg_connection
    ) -> None:
        before = pg_connection.execute("SELECT count(*) FROM core_events").fetchone()[0]

        response = api.post("/api/campaign/bgm", json={"bgm_phase": "critical"})

        assert response.status_code == 200
        assert response.json()["bgm_phase"] == "critical"
        after = pg_connection.execute("SELECT count(*) FROM core_events").fetchone()[0]
        # experience-contract.md: BGM switching is manual and non-reactive
        # by design -- no cue rides on it.
        assert after == before


class TestPauseAndResume:
    def test_pause_then_double_pause_is_409(
        self, api: TestClient, running_exercise_id: str
    ) -> None:
        assert api.post("/api/campaign/pause").status_code == 200
        assert api.post("/api/campaign/pause").status_code == 409

    def test_resume_without_pause_is_409(self, api: TestClient, running_exercise_id: str) -> None:
        assert api.post("/api/campaign/resume").status_code == 409

    def test_resume_shifts_ends_at_forward_by_the_pause_duration(
        self,
        api: TestClient,
        exercise_store: ExerciseStore,
        running_exercise_id: str,
        exercise_clock,
    ) -> None:
        original_ends_at = exercise_store.current().ends_at

        assert api.post("/api/campaign/pause").status_code == 200
        exercise_clock.advance(timedelta(minutes=3))

        response = api.post("/api/campaign/resume")

        assert response.status_code == 200
        assert response.json()["paused"] is False
        assert response.json()["ends_at"] == (
            (original_ends_at + timedelta(minutes=3)).isoformat()
        )


class TestObjectiveCompleteCue:
    def test_submitting_the_correct_flag_emits_a_public_objective_complete_event(
        self, exercise_store: ExerciseStore, pg_connection, exercise_clock
    ) -> None:
        flag = "flag{" + "a" * 32 + "}"
        submittable = Scenario.model_validate(
            {
                "id": "submit-01",
                "name": "Submission scenario",
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
        exercise_store.start(submittable, roster())
        app = create_app(
            ScenarioCatalog((submittable,)),
            exercise_store=exercise_store, conn=pg_connection,
            flag_source=FixtureFlagSource(flag),
            token_map={"instructor-secret": "instructor", "red-secret": "red"},
            campaign_clock=exercise_clock,
        )
        # client= is the roster's own source IP (roster() in
        # test_exercises.py): _player_or_403 needs a real, roster-matching
        # inet-castable address -- TestClient's default fake peer
        # ("testclient") isn't one.
        api = TestClient(
            app, client=("10.167.30.11", 12345), headers={"Authorization": "Bearer red-secret"}
        )

        response = api.post(
            "/api/submissions", json={"objective_id": "capture_flag", "flag": flag}
        )

        assert response.status_code == 200
        assert response.json()["accepted"] is True
        row = pg_connection.execute(
            "SELECT event_type, event FROM core_events "
            "WHERE event_type = 'campaign.objective_complete'"
        ).fetchone()
        assert row[0] == "campaign.objective_complete"
        assert row[1]["visibility"] == "public"
        assert row[1]["target"]["objective_id"] == "capture_flag"
        assert row[1]["target"]["evaluation"] == "submission"

    def test_resubmitting_an_already_completed_objective_does_not_replay_the_cue(
        self, exercise_store: ExerciseStore, pg_connection, exercise_clock
    ) -> None:
        flag = "flag{" + "b" * 32 + "}"
        submittable = Scenario.model_validate(
            {
                "id": "submit-02",
                "name": "Submission scenario 2",
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
        exercise_store.start(submittable, roster())
        app = create_app(
            ScenarioCatalog((submittable,)),
            exercise_store=exercise_store, conn=pg_connection,
            flag_source=FixtureFlagSource(flag),
            token_map={"instructor-secret": "instructor", "red-secret": "red"},
            campaign_clock=exercise_clock,
        )
        api = TestClient(
            app, client=("10.167.30.11", 12345), headers={"Authorization": "Bearer red-secret"}
        )
        body = {"objective_id": "capture_flag", "flag": flag}

        api.post("/api/submissions", json=body)
        api.post("/api/submissions", json=body)

        count = pg_connection.execute(
            "SELECT count(*) FROM core_events WHERE event_type = 'campaign.objective_complete'"
        ).fetchone()[0]
        assert count == 1
