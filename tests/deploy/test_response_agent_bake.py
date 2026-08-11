"""#17 golden image 結構契約；真烤圖與開機狀態仍由大主機 T4 證明。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BAKE = (ROOT / "deploy" / "range-target" / "bake.sh").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "range" / "build-golden-target.sh").read_text(encoding="utf-8")
CLIENT_SOURCE = "\n".join(
    (ROOT / "src" / "purple" / "response" / name).read_text(encoding="utf-8")
    for name in ("http_link.py", "service.py")
)


def test_response_agent_is_the_fourth_managed_systemd_unit():
    assert "purplescope-response-agent.service" in BAKE
    assert "GOLDEN-RESPONSE-STATE" in BUILD


def test_response_agent_unit_has_only_outbound_client_entrypoint():
    unit = BAKE.split("purplescope-response-agent.service <<'UNIT'", 1)[1].split("\nUNIT", 1)[0]
    assert "python3 -m purple.response.service" in unit
    assert "ListenStream" not in unit
    assert "socket" not in unit.lower()


def test_response_agent_client_opens_no_inbound_socket():
    for forbidden in ("http.server", "ThreadingHTTPServer", ".listen(", ".accept("):
        assert forbidden not in CLIENT_SOURCE


def test_golden_injects_the_existing_response_package_not_a_second_agent():
    for module in ("agent.py", "queue.py", "direct_block.py", "http_link.py", "service.py"):
        assert f"src/purple/response/{module}" in BUILD
    # import purple.harness.schema 會先載入 package __init__；它的既有 imports 也必須在 image。
    for module in ("attacker.py", "loki_probe.py", "schema.py", "waiting.py"):
        assert f"src/purple/harness/{module}" in BUILD


def test_ipset_runtime_is_installed_in_the_golden_image():
    assert "install -y -q ipset iptables" in BAKE
