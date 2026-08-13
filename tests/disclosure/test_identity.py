"""共用服務身分契約（#52 B2）—— token 換出 identity 的那一份規則。

兩個對外出口（Evidence API、Range Core API）都用這裡的函式。規則只有一份，
所以「其中一邊漏做」不再是可能發生的事 —— 與 B1 把遮蔽規則收成單一來源同理。
"""

from disclosure import (
    CALLER_CLEARANCE,
    extract_token,
    load_service_tokens,
    resolve_identity,
)

PREFIX = "TEST_TOKEN_"
TOKEN_MAP = {"blue-secret": "blue"}


class TestLoadServiceTokens:
    def test_builds_token_to_identity_map_from_env(self):
        env = {"TEST_TOKEN_BLUE": "blue-secret", "TEST_TOKEN_PURPLE": "purple-secret"}
        assert load_service_tokens(PREFIX, env) == {
            "blue-secret": "blue",
            "purple-secret": "purple",
        }

    def test_empty_env_yields_empty_map_fail_closed(self):
        """沒設任何 token 變數 → 空表，不是「預設放行」。"""
        assert load_service_tokens(PREFIX, {}) == {}

    def test_identity_without_env_var_has_no_token(self):
        tokens = load_service_tokens(PREFIX, {"TEST_TOKEN_BLUE": "blue-secret"})
        assert "instructor" not in tokens.values()

    def test_only_known_identities_are_loadable(self):
        """環境變數裡冒出 clearance 表沒有的身分，不會因為有人設了變數就存在。"""
        tokens = load_service_tokens(PREFIX, {"TEST_TOKEN_CEO": "ceo-secret"})
        assert tokens == {}
        assert "ceo" not in CALLER_CLEARANCE

    def test_prefix_separates_token_namespaces(self):
        """一個出口的 token 換不出另一個出口的身分 —— prefix 就是命名空間邊界。"""
        env = {"TEST_TOKEN_BLUE": "blue-secret"}
        assert load_service_tokens("OTHER_SERVICE_TOKEN_", env) == {}


class TestExtractToken:
    def test_reads_bearer_token(self):
        assert extract_token({"Authorization": "Bearer blue-secret"}) == "blue-secret"

    def test_missing_header_is_none(self):
        assert extract_token({}) is None

    def test_non_bearer_scheme_is_ignored(self):
        assert extract_token({"Authorization": "Basic blue-secret"}) is None

    def test_empty_bearer_value_is_none(self):
        assert extract_token({"Authorization": "Bearer "}) is None


class TestResolveIdentity:
    def test_known_token_resolves(self):
        assert resolve_identity("blue-secret", TOKEN_MAP) == "blue"

    def test_unknown_token_is_none_never_a_default(self):
        assert resolve_identity("not-a-token", TOKEN_MAP) is None

    def test_missing_token_is_none(self):
        assert resolve_identity(None, TOKEN_MAP) is None

    def test_forged_identity_string_does_not_resolve_as_token(self):
        """把想冒充的身分字串直接當 token 送 —— 查無此 token。"""
        for forged in ("purple", "instructor", "blue"):
            assert resolve_identity(forged, TOKEN_MAP) is None
