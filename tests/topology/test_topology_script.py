"""票 12 —— 拓樸契約腳本的存在性與自洽（環境斷言，非 TDD）。

真正的網段實測要在四區環境跑 `scripts/verify_topology.py`（workstream 6）。
本檔只確保：腳本在、四條契約都被涵蓋、且缺環境時不會 fake pass。
"""

from pathlib import Path

from purple.topology_check import (  # 由腳本邏輯抽出的可測部分
    EXPECTED_KALI,
    MGMT_PORTS,
    check_source_ips_distinguishable,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_topology.py"
MODULE = Path(__file__).resolve().parents[2] / "src" / "purple" / "topology_check.py"


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


def test_six_kali_must_be_distinguishable():
    assert EXPECTED_KALI == 6
    # 塌成兩個主機 IP → 失敗
    assert check_source_ips_distinguishable(["10.0.0.1", "10.0.0.2"])
    # 六個不同 → 通過（無失敗訊息）
    assert not check_source_ips_distinguishable([f"10.167.223.{i}" for i in range(1, 7)])
