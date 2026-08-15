"""`generate()`（#131）—— 用一台真的本機 HTTP server 當 Ollama 的替身，
同 `tests/range_core/test_response_dispatch.py` 的手法。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from purple.ai.ollama_client import generate

MODEL = "qwen2.5:3b"


class _FakeOllama(BaseHTTPRequestHandler):
    """假冒 Ollama 的 `/api/generate`：記下收到什麼，回傳可設定的內容。"""

    received: list[dict] = []
    status_to_return = 200
    response_body: bytes = b'{"response": "fake narrative"}'

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append(body)
        self.send_response(self.status_to_return)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.response_body)))
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *args):
        return


@pytest.fixture
def upstream():
    _FakeOllama.received = []
    _FakeOllama.status_to_return = 200
    _FakeOllama.response_body = b'{"response": "fake narrative"}'
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", _FakeOllama
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestSuccessfulGeneration:
    def test_returns_the_response_text(self, upstream):
        base_url, _ = upstream

        assert generate("summarize this", base_url=base_url, model=MODEL) == "fake narrative"

    def test_sends_model_prompt_and_stream_false(self, upstream):
        base_url, handler = upstream

        generate("summarize this", base_url=base_url, model=MODEL)

        [body] = handler.received
        assert body["model"] == MODEL
        assert body["prompt"] == "summarize this"
        assert body["stream"] is False
        assert "system" not in body  # 沒傳 system 就不該出現這個 key

    def test_system_prompt_is_included_when_given(self, upstream):
        base_url, handler = upstream

        generate("summarize this", system="be terse", base_url=base_url, model=MODEL)

        [body] = handler.received
        assert body["system"] == "be terse"

    def test_strips_surrounding_whitespace(self, upstream):
        base_url, handler = upstream
        handler.response_body = b'{"response": "  padded text  \\n"}'

        assert generate("x", base_url=base_url, model=MODEL) == "padded text"


class TestFailureIsNoneNotAnException:
    def test_non_2xx_is_none(self, upstream):
        base_url, handler = upstream
        handler.status_to_return = 500

        assert generate("x", base_url=base_url, model=MODEL) is None

    def test_unreachable_server_is_none(self):
        # 埠 1 幾乎保證連不上（特權埠、沒人聽）。
        assert generate("x", base_url="http://127.0.0.1:1", model=MODEL, timeout_s=1.0) is None

    def test_malformed_json_is_none(self, upstream):
        base_url, handler = upstream
        handler.response_body = b"not json"

        assert generate("x", base_url=base_url, model=MODEL) is None

    def test_missing_response_field_is_none(self, upstream):
        base_url, handler = upstream
        handler.response_body = b'{"model": "qwen2.5:3b"}'

        assert generate("x", base_url=base_url, model=MODEL) is None

    def test_empty_response_text_is_none(self, upstream):
        base_url, handler = upstream
        handler.response_body = b'{"response": "   "}'

        assert generate("x", base_url=base_url, model=MODEL) is None


class TestConfiguration:
    def test_falls_back_to_env_vars(self, monkeypatch, upstream):
        base_url, handler = upstream
        monkeypatch.setenv("OLLAMA_BASE_URL", base_url)
        monkeypatch.setenv("OLLAMA_MODEL", "custom-model")

        assert generate("x") == "fake narrative"

        [body] = handler.received
        assert body["model"] == "custom-model"

    def test_explicit_args_win_over_env_vars(self, monkeypatch, upstream):
        base_url, handler = upstream
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("OLLAMA_MODEL", "env-model")

        assert generate("x", base_url=base_url, model=MODEL) == "fake narrative"

        [body] = handler.received
        assert body["model"] == MODEL
