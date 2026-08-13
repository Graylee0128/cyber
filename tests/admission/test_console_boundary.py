"""中控 UI 的核心不變量：畫面絕不外洩禁忌欄位。

對應 spec §1（無分數/objective/告警/偵測）、§6.5.1（無藍隊個人分數/排名）、
§5.4（七禁欄位）。這條不變量目前只靠模板手寫的自律撐著，用這個測試接住，
避免真資料替換後不小心漏渲染出禁忌欄位。
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from admission.api import AdmissionSettings, create_app


def client(pg_connection):
    return TestClient(create_app(conn=pg_connection, settings=AdmissionSettings(
        instructor_tokens={"console-token": "teacher"}, session_ttl_seconds=60,
        disable_range_publication=True,
    )))

# 禁忌 token：分數/排名/告警/偵測相關字樣，不分大小寫
FORBIDDEN_TOKENS = [
    "score",
    "rank",
    "ranking",
    "threshold",
    "payload",
    "query",
    "detection",
    "objective",
    "alert",
]


def test_console_red_team_has_no_forbidden_tokens(pg_connection):
    resp = client(pg_connection).get("/admission/instructor/EX-TEST/console?team=red", headers={"Authorization": "Bearer console-token"})
    assert resp.status_code == 200
    body_lower = resp.text.lower()
    for token in FORBIDDEN_TOKENS:
        assert not re.search(rf"\b{token}\b", body_lower), (
            f"禁忌 token '{token}' 出現在紅隊畫面渲染結果中"
        )


def test_console_blue_team_has_no_forbidden_tokens(pg_connection):
    resp = client(pg_connection).get("/admission/instructor/EX-TEST/console?team=blue", headers={"Authorization": "Bearer console-token"})
    assert resp.status_code == 200
    body_lower = resp.text.lower()
    for token in FORBIDDEN_TOKENS:
        assert not re.search(rf"\b{token}\b", body_lower), (
            f"禁忌 token '{token}' 出現在藍隊畫面渲染結果中（§6.5.1 藍隊不轉個人分數）"
        )
