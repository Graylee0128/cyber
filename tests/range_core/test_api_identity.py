"""Range Core 對外端點的服務身分（#52 B2）。

Evidence API 那一半的對應測試在 `tests/evidence/test_service.py`。這裡驗的是
第二個出口：`range_core` 的 FastAPI 端點同樣不接受呼叫端自填身分。

把 `create_app` 的 `require_identity` 依賴拿掉時，本檔每一條都要變紅。
"""

from fastapi.testclient import TestClient

from range_core.api import TOKEN_ENV_PREFIX, create_app
from range_core.scenarios import ScenarioCatalog

TOKEN_MAP = {"instructor-secret": "instructor", "red-secret": "red"}


def _client(token_map=TOKEN_MAP) -> TestClient:
    return TestClient(create_app(ScenarioCatalog(scenarios=()), token_map=token_map))


class TestTokenRequired:
    def test_no_token_is_400(self):
        response = _client().get("/api/scenarios")
        assert response.status_code == 400

    def test_unknown_token_is_403(self):
        response = _client().get(
            "/api/scenarios", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 403

    def test_known_token_is_served(self):
        response = _client().get(
            "/api/scenarios", headers={"Authorization": "Bearer instructor-secret"}
        )
        assert response.status_code == 200

    def test_no_tokens_configured_rejects_everything_fail_closed(self):
        """沒有任何 token 設定 → 全部拒絕，而不是「沒設定就放行」。"""
        response = _client(token_map={}).get(
            "/api/scenarios", headers={"Authorization": "Bearer instructor-secret"}
        )
        assert response.status_code == 403


class TestSelfReportProtection:
    """呼叫者宣稱自己是 purple／instructor 必須無效 —— 身分只能由 token 換出。"""

    def test_legacy_identity_header_grants_nothing(self):
        response = _client().get(
            "/api/scenarios", headers={"X-Purple-Identity": "instructor"}
        )
        assert response.status_code == 400

    def test_identity_string_sent_as_token_is_rejected(self):
        for forged in ("instructor", "purple", "blue"):
            response = _client().get(
                "/api/scenarios", headers={"Authorization": f"Bearer {forged}"}
            )
            assert response.status_code == 403, forged

    def test_mutating_endpoint_is_protected_too(self):
        """不是只有讀取端點要驗 —— reset 這種會改狀態的更不能匿名打。"""
        response = _client().post("/api/exercises/reset")
        assert response.status_code == 400


class TestTokenNeverLeaked:
    def test_unknown_token_not_echoed_in_response(self):
        response = _client().get(
            "/api/scenarios", headers={"Authorization": "Bearer totally-wrong-token"}
        )
        assert "totally-wrong-token" not in response.text


def test_token_namespace_is_range_core_specific():
    """Range Core 的 token 命名空間與 Evidence API 分開，一邊外洩不會連坐另一邊。"""
    assert TOKEN_ENV_PREFIX == "RANGE_CORE_TOKEN_"
