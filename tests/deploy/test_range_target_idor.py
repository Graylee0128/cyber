"""FINAL The Leak（校園學生資料自助查詢，#153 Campaign Pack v1）攻擊面的真實驗證。

不是模擬：起真的 `ThreadingHTTPServer`，真的自助核發一個 token、真的用它讀走
「別人」的學生資料、真的批次跑很多筆——T1087（帳號列舉核發）→ T1213（IDOR
讀取）→ T1567（批次外洩）三步全部走一次。這是刻意的 detection-gap 教學案例：
T1213/T1567 沒有對應規則，這條測試證明的是漏洞本身，不是偵測覆蓋。
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_APP_PATH = Path(__file__).resolve().parents[2] / "deploy" / "range-target" / "app.py"


def _load_app_module(tmp_path: Path):
    os.environ["TARGET_LOG_PATH"] = str(tmp_path / "app.log")
    spec = importlib.util.spec_from_file_location("range_target_app_final", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app_module(tmp_path):
    return _load_app_module(tmp_path)


@pytest.fixture
def live_server(app_module):
    server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    try:
        yield port
    finally:
        server.shutdown()


class TestFakeRecordIsDeterministicAcrossProcesses:
    def test_same_id_produces_the_same_record_content(self, app_module):
        # hashlib，不是內建 hash()：後者對 str 有 per-process 隨機種子，重啟後
        # 同一個 id 會讀到不同數字——那樣「同一份資料」這個宣稱就不成立了。
        rec1 = app_module._fake_student_record("s1001")
        rec2 = app_module._fake_student_record("s1001")
        assert rec1 == rec2


class TestIdorClassification:
    def test_matching_id_and_token_owner_is_not_flagged(self, app_module):
        token = app_module._issue_student_token("s1001")
        assert app_module._looks_like_idor_access("s1001", token) is False

    def test_mismatched_id_is_flagged(self, app_module):
        token = app_module._issue_student_token("s1001")
        assert app_module._looks_like_idor_access("s9999", token) is True

    def test_unknown_token_is_not_flagged_as_idor_it_is_simply_invalid(self, app_module):
        # 語意區分：token 根本無效跟「有效 token 但讀錯 id」是兩件事，前者在
        # `_token_is_valid` 就會被擋（401），不該混進 IDOR 分類。
        assert app_module._looks_like_idor_access("s1001", "not-a-real-token") is False


class TestSelfServiceTokenGrantsAuthenticationOnly:
    """真 HTTP round-trip。T1087（核發）→ T1213（IDOR 讀）→ T1567（批次外洩）。"""

    def test_token_issuance_requires_a_claimed_student_id(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/students/token")
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()

    def test_token_issuance_does_not_verify_identity(self, live_server):
        # 宣告是誰就核發給誰的 token——沒有密碼、沒有任何身分證明。
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/students/token?student_id=s1001")
        resp = conn.getresponse()
        assert resp.status == 201
        token = resp.read().decode().strip()
        assert token

    def test_invalid_token_is_denied(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/students/s1001/records?token=not-a-real-token")
        resp = conn.getresponse()
        assert resp.status == 401
        resp.read()

    def test_own_record_is_readable_with_own_token(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/students/token?student_id=s1001")
        token = conn.getresponse().read().decode().strip()

        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", f"/students/s1001/records?token={token}")
        resp = conn.getresponse()
        assert resp.status == 200
        rec = json.loads(resp.read())
        assert rec["student_id"] == "s1001"

    def test_idor_the_same_token_reads_a_different_students_record(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/students/token?student_id=s1001")
        token = conn.getresponse().read().decode().strip()

        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", f"/students/s9999/records?token={token}")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200, "the endpoint must not check object ownership -- that is the vulnerability"
        rec = json.loads(body)
        assert rec["student_id"] == "s9999"

    def test_bulk_exfiltration_iterates_many_ids_with_one_token(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/students/token?student_id=s1001")
        token = conn.getresponse().read().decode().strip()

        collected = []
        for i in range(15):
            sid = f"s{3000 + i}"
            conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
            conn.request("GET", f"/students/{sid}/records?token={token}")
            resp = conn.getresponse()
            assert resp.status == 200
            collected.append(json.loads(resp.read()))

        assert len(collected) == 15
        assert len({r["student_id"] for r in collected}) == 15
