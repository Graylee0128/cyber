from pathlib import Path

import yaml


ROOT = Path(__file__).parents[2]
NGINX = ROOT / "deploy" / "edge" / "nginx.conf"
COMPOSE = ROOT / "docker-compose.yml"


def _nginx() -> str:
    return NGINX.read_text(encoding="utf-8")


def test_terminal_proxy_uses_an_internal_admission_subrequest():
    text = _nginx()

    assert "set $requested_terminal $terminal;" in text
    assert "auth_request /_auth_ttyd;" in text
    assert "internal;" in text
    assert "set $admission_upstream admission:8000;" in text
    assert "http://$admission_upstream/admission/auth/ttyd/$requested_terminal" in text
    assert "proxy_set_header Cookie $http_cookie;" in text


def test_proxy_target_only_comes_from_admission_response_header():
    text = _nginx()

    assert "auth_request_set $ttyd_upstream $upstream_http_x_ttyd_upstream;" in text
    assert "proxy_pass http://$ttyd_upstream;" in text
    assert "proxy_pass http://$http_" not in text
    assert "proxy_set_header X-Ttyd-Upstream \"\";" in text


def test_terminal_proxy_preserves_websocket_upgrade():
    text = _nginx()

    assert "proxy_http_version 1.1;" in text
    assert "proxy_set_header Upgrade $http_upgrade;" in text
    assert "proxy_set_header Connection $connection_upgrade;" in text


def test_edge_config_contains_no_database_or_credential_material():
    text = _nginx().lower()

    for forbidden in ("postgres", "pg_dsn", "password", "secret", "token", "hmac"):
        assert forbidden not in text


def test_admission_e2e_services_form_a_narrow_access_plane():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    expected = {
        "admission-postgres",
        "admission",
        "admission-edge",
        "admission-red-terminal",
        "admission-blue-terminal",
    }

    assert expected <= services.keys()
    for name in expected:
        assert "admission-e2e" in services[name].get("profiles", [])
    assert services["admission"]["depends_on"]["admission-postgres"]["condition"] == "service_healthy"
    assert services["admission"]["environment"]["ADMISSION_DISABLE_RANGE_PUBLICATION"] == "1"

    edge = services["admission-edge"]
    assert "z-mgmt" not in edge["networks"]
    assert not edge.get("environment")
    assert not edge.get("secrets")
    assert set(services["admission-red-terminal"]["networks"]) == {"z-red"}
    assert set(services["admission-blue-terminal"]["networks"]) == {"z-blue"}
