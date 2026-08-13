"""端點層級 clearance（#49，審 #33 對上 #52 B2 時發現）。

B2 只保證身分不可自報，**誰可以呼叫哪個端點**它明確留給 WS5。#33 讓這件事變
具體：source-IP 歸屬意味著 Kali 主機直連 Range Core，紅隊機器上因此會有一個
red token —— 沒有本表的話，那個 token 也能把整場演練 reset 掉。

把 `ENDPOINT_MIN_CLEARANCE` 清空時，本檔的 403 測試必須變紅。
"""

from disclosure import CALLER_CLEARANCE
from fastapi.testclient import TestClient

from range_core.api import ENDPOINT_MIN_CLEARANCE, create_app
from range_core.scenarios import ScenarioCatalog

TOKEN_MAP = {
    "red-secret": "red",
    "blue-secret": "blue",
    "instructor-secret": "instructor",
}
START_BODY = {
    "scenario_id": "whatever",
    "players": [{"player_id": "red-alice", "source_ip": "10.167.30.11"}],
}


def _client(identity: str) -> TestClient:
    app = create_app(ScenarioCatalog(scenarios=()), token_map=TOKEN_MAP)
    return TestClient(app, headers={"Authorization": f"Bearer {identity}-secret"})


class TestLifecycleNeedsInstructor:
    def test_red_cannot_reset_the_running_exercise(self):
        """輸到一半的紅隊玩家不能把記分板清掉。"""
        assert _client("red").post("/api/exercises/reset").status_code == 403

    def test_blue_cannot_reset_either(self):
        assert _client("blue").post("/api/exercises/reset").status_code == 403

    def test_red_cannot_start_an_exercise(self):
        assert _client("red").post("/api/exercises/start", json=START_BODY).status_code == 403

    def test_instructor_clearance_satisfies_every_row(self):
        """instructor 一定過得了門 —— 真正打通端點的證據在 test_api_scoring.py 的
        `start()`（帶 instructor token、對真 PG 開演練、期望 201）。"""
        assert all(
            CALLER_CLEARANCE["instructor"] >= required
            for required in ENDPOINT_MIN_CLEARANCE.values()
        )


class TestGameplayStaysOpenToPlayers:
    def test_reading_scenarios_needs_only_a_valid_token(self):
        """遊戲端點的預設就是「任何合法參與者」—— 玩家本來就該打得到。"""
        assert _client("red").get("/api/scenarios").status_code == 200


class TestPolicyIsData:
    def test_table_lists_only_the_privileged_endpoints(self):
        assert set(ENDPOINT_MIN_CLEARANCE) == {
            ("POST", "/api/exercises/start"),
            ("POST", "/api/exercises/reset"),
        }
