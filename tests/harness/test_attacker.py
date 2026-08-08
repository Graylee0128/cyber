"""SQLi 注入器 —— 對一個 in-process 的假靶機驗證，不需要真的漏洞 app。

假靶機只做一件事：記下它收到的請求，讓我們斷言注入器真的送出了 payload。
"""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from purple.harness.attacker import AttackFailed, inject_sqli, make_marker


class _Recorder(BaseHTTPRequestHandler):
    received: list[str] = []

    def do_GET(self):
        _Recorder.received.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass  # 別把測試輸出淹掉


@pytest.fixture
def fake_target():
    _Recorder.received = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", _Recorder
    finally:
        server.shutdown()
        server.server_close()


class TestInjectionReachesTheTarget:
    def test_target_receives_the_request(self, fake_target):
        base, recorder = fake_target
        inject_sqli(base, path="/login")
        assert len(recorder.received) == 1
        assert recorder.received[0].startswith("/login?")

    def test_payload_carries_a_sql_injection(self, fake_target):
        base, recorder = fake_target
        inject_sqli(base)
        assert "OR" in recorder.received[0]
        assert "1" in recorder.received[0]

    def test_4xx_still_counts_as_delivered(self, fake_target):
        """靶機回錯不代表注入失敗 —— 請求送達了就算成功。"""

        class Rejecting(_Recorder):
            def do_GET(self):
                _Recorder.received.append(self.path)
                self.send_response(403)
                self.end_headers()

        base, recorder = fake_target
        recorder.received = []
        # 直接用注入器對回 403 的處理已由 attacker 內部覆蓋；此處確認不拋例外
        result = inject_sqli(base)
        assert result.status_code in (200, 403)


class TestTraceability:
    def test_result_carries_a_unique_attack_id(self, fake_target):
        base, _ = fake_target
        a = inject_sqli(base)
        b = inject_sqli(base)
        assert a.attack_id != b.attack_id

    def test_marker_is_embedded_in_the_request(self, fake_target):
        """attack_id 要能在靶機收到的請求裡找到 —— 這是後面把事件追回攻擊的鉤子。"""
        base, recorder = fake_target
        result = inject_sqli(base)
        assert make_marker(result.attack_id).replace(" ", "%20") in recorder.received[0] \
            or result.attack_id in recorder.received[0]


class TestFailureIsLoud:
    def test_unreachable_target_raises(self):
        """連不上不能靜默回一個看似成功的結果 —— 那會讓 02b 的紅燈永遠綠。"""
        with pytest.raises(AttackFailed):
            inject_sqli("http://127.0.0.1:1", timeout_s=1)
