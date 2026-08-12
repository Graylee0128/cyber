"""票 12 —— 拓樸契約腳本的存在性與自洽（環境斷言，非 TDD）。

真正的網段實測要在四區環境跑 `scripts/verify_topology.py`（workstream 6）。
本檔只確保：腳本在、四條契約都被涵蓋、且缺環境時不會 fake pass。
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from purple.topology_check import (  # 由腳本邏輯抽出的可測部分
    EXPECTED_KALI,
    MGMT_DENIED_PORT,
    MGMT_PORTS,
    check_source_ips_distinguishable,
    check_target_to_mgmt,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_topology.py"
MODULE = Path(__file__).resolve().parents[2] / "src" / "purple" / "topology_check.py"
BUILD_RANGE = (
    Path(__file__).resolve().parents[2] / "scripts" / "range" / "build-range.sh"
)
VERIFY_RANGE = (
    Path(__file__).resolve().parents[2] / "scripts" / "range" / "verify-range.sh"
)
CONTRACT1_PROBE = (
    Path(__file__).resolve().parents[2] / "scripts" / "range" / "contract1_probe.py"
)


def _load_contract1_probe():
    spec = importlib.util.spec_from_file_location("contract1_probe", CONTRACT1_PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ProbeSocket:
    def __init__(self, *, block_denied: bool):
        self.block_denied = block_denied

    def settimeout(self, _timeout):
        pass

    def connect(self, address):
        if self.block_denied and address[1] == MGMT_DENIED_PORT:
            raise OSError("blocked by Contract 1")

    def close(self):
        pass


def test_script_exists():
    assert SCRIPT.exists()


def test_logic_covers_the_three_contract_checks():
    """契約1/2/3 的判定住在可匯入的 topology_check 模組。"""
    text = MODULE.read_text(encoding="utf-8")
    for marker in ("契約1", "契約2", "契約3"):
        assert marker in text, f"topology_check 沒涵蓋 {marker}"


def test_script_mentions_collectors_and_kali():
    """collector 位置與六台 kali 可分辨性在 CLI 說明中被涵蓋。"""
    text = SCRIPT.read_text(encoding="utf-8")
    for marker in ("collector", "kali"):
        assert marker in text, f"腳本沒涵蓋 {marker}"


def test_script_offers_compose_mode():
    """票 #15：CLI 提供 --compose 模式，對 compose 網段歸屬驗可強制的區規則。"""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--compose" in text


def test_each_zone_fails_without_its_peer():
    """票 #13：target/mgmt/red 三個 zone 各驗一條契約，缺對端 IP 都必須 exit 2。

    每條契約要從對應區跑（契約 2 在 target 跑會連到自己而假通），所以三個 zone
    都是獨立入口；缺對端不能 fake pass。"""
    import subprocess
    import sys

    for zone in ("target", "mgmt", "red"):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--from-zone", zone],
            capture_output=True, text=True,
        )
        assert r.returncode == 2, f"--from-zone {zone} 缺對端應 exit 2，得 {r.returncode}"


def test_missing_environment_does_not_fake_pass():
    """缺 --mgmt/--target 時退出碼必須非 0。"""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode != 0


def test_the_three_cross_generation_ports_are_named():
    assert set(MGMT_PORTS) == {3100, 9090, 4317}
    probe = _load_contract1_probe()
    assert probe.PORTS == MGMT_PORTS
    assert probe.DENIED_PORT == MGMT_DENIED_PORT


def test_contract1_accepts_only_telemetry_ports(monkeypatch):
    def fake_reachable(_host, port, timeout=3.0):
        return port in MGMT_PORTS

    monkeypatch.setattr("purple.topology_check.reachable", fake_reachable)
    assert check_target_to_mgmt("10.167.10.10") == []


def test_contract1_flags_reachable_non_telemetry_port(monkeypatch):
    monkeypatch.setattr("purple.topology_check.reachable", lambda *_args, **_kwargs: True)
    fails = check_target_to_mgmt("10.167.10.10")
    assert any(f":{MGMT_DENIED_PORT}" in fail and "竟然通了" in fail for fail in fails)


def test_vm_contract1_probe_passes_three_ports_and_blocked_canary(monkeypatch, capsys):
    probe = _load_contract1_probe()
    monkeypatch.setattr(
        probe,
        "socket",
        SimpleNamespace(socket=lambda: _ProbeSocket(block_denied=True)),
    )
    monkeypatch.setattr(probe.sys, "argv", ["contract1_probe.py", "mgmt", "nonce"])
    probe.main()
    output = capsys.readouterr().out
    for port in MGMT_PORTS:
        assert f":{port} 通" in output
    assert f":{MGMT_DENIED_PORT} 不通" in output
    assert "SLICE2A-RESULT: PASS" in output


def test_vm_contract1_probe_fails_when_deny_canary_is_reachable(monkeypatch, capsys):
    probe = _load_contract1_probe()
    monkeypatch.setattr(
        probe,
        "socket",
        SimpleNamespace(socket=lambda: _ProbeSocket(block_denied=False)),
    )
    monkeypatch.setattr(probe.sys, "argv", ["contract1_probe.py", "mgmt", "nonce"])
    probe.main()
    output = capsys.readouterr().out
    assert f":{MGMT_DENIED_PORT} 竟然通了" in output
    assert f"SLICE2A-RESULT: FAIL [{MGMT_DENIED_PORT}]" in output


def test_vm_verifier_rejects_old_probe_without_deny_evidence():
    text = VERIFY_RANGE.read_text(encoding="utf-8")
    missing_deny_evidence = 'elif ! grep -q "非 telemetry :22 不通"'
    accepts_pass = 'elif grep -q "SLICE2A-RESULT: PASS"'
    assert missing_deny_evidence in text
    assert text.index(missing_deny_evidence) < text.index(accepts_pass)


def test_build_range_enforces_contract1_allowlist_and_runs_deny_canary():
    text = BUILD_RANGE.read_text(encoding="utf-8")
    assert (
        "ip saddr $TARGET_CIDR ip daddr $MGMT_CIDR "
        "tcp dport { 3100, 9090, 4317 } accept"
    ) in text
    assert "--ports 3100,9090,4317,22" in text


def test_six_kali_must_be_distinguishable():
    assert EXPECTED_KALI == 6
    # 塌成兩個主機 IP → 失敗
    assert check_source_ips_distinguishable(["10.0.0.1", "10.0.0.2"])
    # 六個不同 → 通過（無失敗訊息）
    assert not check_source_ips_distinguishable([f"10.167.223.{i}" for i in range(1, 7)])
