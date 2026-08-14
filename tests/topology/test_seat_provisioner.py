"""#62 Seat Provisioner Agent —— 純函式邏輯（輪詢/回報/孤兒回收）用假 client／
builder 測，不碰真的 docker/OVS（那段見 #62 PR 的 T3/T4 證據，本檔只測 T1）。
"""

from __future__ import annotations

import pytest

from scripts.range.seat_provisioner import (
    ProvisionerConfig,
    _allow_seat_pair,
    _ensure_blue_seat_pair_isolation,
    _ensure_docker_user_drop,
    _mac_for_ip,
    _revoke_ip_flows,
    _seat_id_from_blue_name,
    host_if_name,
    run,
    sweep_orphans,
)


def test_mac_for_ip_is_unique_and_deterministic():
    """VM 上實測抓到：不明確指定 MAC，核心自動配發會撞號（好幾個容器的 eth0
    配到同一個 MAC，IPv6 甚至報 dadfailed），同 VLAN 內誰都 ARP 不到誰，連
    自己的 gateway 都 ping 不通。這是比 flow 規則更底層、影響紅藍兩隊的 bug。
    """
    assert _mac_for_ip("10.167.60.12") == "02:00:0a:a7:3c:0c"
    assert _mac_for_ip("10.167.60.12") == _mac_for_ip("10.167.60.12")  # 決定性
    assert _mac_for_ip("10.167.60.12") != _mac_for_ip("10.167.60.13")  # 不同 IP 不撞號
    assert _mac_for_ip("10.167.60.12")[:2] == "02"  # locally-administered 前綴


def test_host_if_name_is_stable_across_interpreter_restarts():
    """真的在 VM 上跑到過：用內建 hash() 命名時，provisioner 重啟一次就在同一個
    seat 上留下兩個 OVS port（見 PR 說明）。crc32 對同一個 seat_id 永遠算出
    同一個名字，跨 subprocess 重跑驗證——單一進程內比較沒有意義，因為
    PYTHONHASHSEED 隨機化是 per-process 的，同進程內兩次呼叫本來就會一樣。
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    seat_id = "fbb51f79-1f4d-4996-b8e2-b3f55b39daed"
    script = (
        f"import sys; sys.path.insert(0, {str(repo_root)!r}); "
        "from scripts.range.seat_provisioner import host_if_name; "
        f"print(host_if_name({seat_id!r}))"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout.strip()
        for seed in ("0", "1", "random")
    }
    assert outputs == {host_if_name(seat_id)}


def test_blue_seat_id_extraction_strips_terminal_prefix():
    """seat-blue-a-<id> / seat-blue-b-<id> 都要還原回同一個 seat_id——terminal
    只是一個字元加連字號，不是 seat_id 的一部分。"""
    assert _seat_id_from_blue_name("seat-blue-a-abc-123") == "abc-123"
    assert _seat_id_from_blue_name("seat-blue-b-abc-123") == "abc-123"


class FakeAdmission:
    def __init__(self, pending_seats=None, active_ids=None):
        self._pending = {"red": [], "blue": []}
        for seat in pending_seats or []:
            self._pending[seat["team"]].append(seat)
        self._active = {"red": set(), "blue": set()}
        for team, ids in (active_ids or {}).items():
            self._active[team] = set(ids)
        self.marked_ready: list[tuple[str, list[dict]]] = []

    def pending(self, team):
        return self._pending[team]

    def active_seat_ids(self, team):
        return self._active[team]

    def mark_ready(self, seat_id, endpoints):
        self.marked_ready.append((seat_id, endpoints))
        return True


def _config(**overrides):
    defaults = dict(
        admission_url="http://admission", admission_token="tok",
        poll_seconds=0, once=True, red_image="purplescope/red-attacker:latest",
        blue_image="purplescope/blue-seat:latest",
    )
    defaults.update(overrides)
    return ProvisionerConfig(**defaults)


def _noop(*_args, **_kwargs):
    pass


def _run(config, **kw):
    kw.setdefault("ensure_isolation", _noop)
    kw.setdefault("ensure_blue_flow_isolation", _noop)
    return run(config, **kw)


# run() 一律會算 RED/BLUE 的 CIDR、bridge 名字給 ensure_isolation／
# ensure_blue_flow_isolation（就算那兩個 callable 本身是 no-op），所以每個
# 測試的 load_zones 假值都要帶這幾把 key，不能空字典。
_ZONES = {
    "RANGE_NET_PREFIX": "10.167", "Z_RED_VLAN": "30", "Z_BLUE_VLAN": "60",
    "RANGE_BRIDGE": "br-range",
}


def test_config_requires_url_and_token(monkeypatch):
    monkeypatch.delenv("ADMISSION_URL", raising=False)
    monkeypatch.delenv("ADMISSION_PROVISIONER_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ADMISSION_URL"):
        ProvisionerConfig.from_env()


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("ADMISSION_URL", "http://a")
    monkeypatch.setenv("ADMISSION_PROVISIONER_TOKEN", "t")
    monkeypatch.setenv("PROVISIONER_POLL_SECONDS", "7")
    config = ProvisionerConfig.from_env()
    assert config.admission_url == "http://a"
    assert config.poll_seconds == 7
    assert config.once is False


def test_pending_red_seat_gets_built_and_marked_ready(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)
    admission = FakeAdmission(pending_seats=[{"seat_id": "s1", "exercise_id": "EX", "team": "red", "kind": "shell"}])
    built = {"endpoints": [{"terminal": "main", "host": "10.167.30.11", "port": 7681}]}

    _run(_config(), client=admission, red_builder=lambda seat_id, **_: built, blue_builder=lambda seat_id, **_: {})

    assert admission.marked_ready == [("s1", built["endpoints"])]


def test_pending_blue_seat_gets_both_terminals_built_and_marked_ready(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)
    admission = FakeAdmission(pending_seats=[{"seat_id": "b1", "exercise_id": "EX", "team": "blue", "kind": "shell"}])
    built = {"endpoints": [
        {"terminal": "a", "host": "10.167.60.12", "port": 7681},
        {"terminal": "b", "host": "10.167.60.13", "port": 7681},
    ]}

    _run(_config(), client=admission, red_builder=lambda seat_id, **_: {}, blue_builder=lambda seat_id, **_: built)

    assert admission.marked_ready == [("b1", built["endpoints"])]


def test_builder_failure_is_skipped_not_fatal(monkeypatch):
    """見檔頭：建置失敗留給 admission 既有的逾時重試處理，provisioner 只要不當掉。"""
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)
    admission = FakeAdmission(pending_seats=[{"seat_id": "s1", "exercise_id": "EX", "team": "red", "kind": "shell"}])

    def failing_builder(seat_id, **_):
        raise RuntimeError("docker daemon unreachable")

    _run(_config(), client=admission, red_builder=failing_builder)  # 不應該拋例外

    assert admission.marked_ready == []


def test_poll_failure_does_not_crash_the_loop(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)

    class BrokenAdmission(FakeAdmission):
        def pending(self, team):
            raise ConnectionError("admission unreachable")

    _run(_config(), client=BrokenAdmission(), red_builder=lambda seat_id, **_: {"endpoints": []})
    # 沒拋例外就是通過；once=True 讓迴圈跑一輪後正常返回。


def test_once_mode_does_not_sleep(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)
    calls = []

    _run(
        _config(once=True), client=FakeAdmission(),
        red_builder=lambda seat_id, **_: {"endpoints": []}, sleep=lambda s: calls.append(s),
    )

    assert calls == []


def test_run_checks_isolation_for_both_vlans_at_startup(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)
    calls = []

    run(
        _config(), client=FakeAdmission(),
        red_builder=lambda seat_id, **_: {"endpoints": []},
        blue_builder=lambda seat_id, **_: {"endpoints": []},
        ensure_isolation=lambda cidr, env: calls.append((cidr, env)),
        ensure_blue_flow_isolation=_noop,
    )

    assert calls == [
        ("10.167.30.0/24", "ALLOW_RED_LATERAL"),
        ("10.167.60.0/24", "ALLOW_BLUE_LATERAL"),
    ]


def test_run_checks_blue_flow_isolation_at_startup(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: _ZONES)
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client, **kw: 0)
    calls = []

    run(
        _config(), client=FakeAdmission(),
        red_builder=lambda seat_id, **_: {"endpoints": []},
        blue_builder=lambda seat_id, **_: {"endpoints": []},
        ensure_isolation=_noop,
        ensure_blue_flow_isolation=lambda bridge, cidr: calls.append((bridge, cidr)),
    )

    assert calls == [("br-range", "10.167.60.0/24")]


def test_sweep_orphans_only_removes_containers_not_in_active_set(monkeypatch):
    """核心正確性：座位已 ready（active）不能被當孤兒拆掉，只有 released/failed
    （不在 active 名單）的容器才拆。這是這支 agent 最容易寫錯、後果最重的一段——
    v1 曾經誤用「不在 pending 名單」當孤兒判準，會在每次重啟時把剛建好、正在
    服務玩家的座位一起砍掉，寫測試時才抓到，見 PR 說明。"""
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["docker", "ps"] and any("seat-red-" in a for a in args):
            return _FakeCompleted("seat-red-still-active\nseat-red-long-gone\n")
        if args[:2] == ["docker", "ps"] and any("seat-blue-" in a for a in args):
            return _FakeCompleted("")
        return _FakeCompleted("")

    monkeypatch.setattr("scripts.range.seat_provisioner.subprocess.run", fake_run)
    admission = FakeAdmission(active_ids={"red": {"still-active"}})

    removed = sweep_orphans(admission)

    assert removed == 1
    rm_calls = [c for c in calls if c[:2] == ["docker", "rm"]]
    assert rm_calls == [["docker", "rm", "-f", "seat-red-long-gone"]]


def test_sweep_orphans_handles_blue_pairs_independently_of_red(monkeypatch):
    """藍隊一個 seat 兩台容器（a／b），兩台都要因為同一個 seat_id 不 active
    而一起被清掉——不能清了 a 漏了 b。"""
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["docker", "ps"] and any("seat-red-" in a for a in args):
            return _FakeCompleted("")
        if args[:2] == ["docker", "ps"] and any("seat-blue-" in a for a in args):
            return _FakeCompleted("seat-blue-a-gone1\nseat-blue-b-gone1\nseat-blue-a-live1\nseat-blue-b-live1\n")
        return _FakeCompleted("")

    monkeypatch.setattr("scripts.range.seat_provisioner.subprocess.run", fake_run)
    admission = FakeAdmission(active_ids={"blue": {"live1"}})

    removed = sweep_orphans(admission)

    assert removed == 2
    removed_names = {c[-1] for c in calls if c[:2] == ["docker", "rm"]}
    assert removed_names == {"seat-blue-a-gone1", "seat-blue-b-gone1"}


def test_docker_user_drop_is_idempotent_and_skipped_when_lateral_allowed(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["iptables", "-nL"]:
            return _FakeCompleted("", returncode=0)
        if args[:2] == ["iptables", "-C"]:
            return _FakeCompleted("", returncode=1)  # 規則不存在，該插入
        return _FakeCompleted("", returncode=0)

    monkeypatch.setattr("scripts.range.seat_provisioner.subprocess.run", fake_run)

    _ensure_docker_user_drop("10.167.60.0/24", "ALLOW_BLUE_LATERAL")
    insert_calls = [c for c in calls if c[:2] == ["iptables", "-I"]]
    assert len(insert_calls) == 1

    calls.clear()
    monkeypatch.setenv("ALLOW_BLUE_LATERAL", "1")
    _ensure_docker_user_drop("10.167.60.0/24", "ALLOW_BLUE_LATERAL")
    assert calls == []  # 開了 lateral，完全不碰 iptables


def test_blue_default_deny_flow_uses_the_blue_cidr(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.range.seat_provisioner.subprocess.run",
        lambda args, **kw: (calls.append(args), _FakeCompleted(""))[1],
    )

    _ensure_blue_seat_pair_isolation("br-range", "10.167.60.0/24")

    assert len(calls) == 1
    assert calls[0][:3] == ["ovs-ofctl", "add-flow", "br-range"]
    assert "nw_src=10.167.60.0/24,nw_dst=10.167.60.0/24,actions=drop" in calls[0][3]


def test_allow_seat_pair_opens_both_directions(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.range.seat_provisioner.subprocess.run",
        lambda args, **kw: (calls.append(args), _FakeCompleted(""))[1],
    )

    _allow_seat_pair("br-range", "10.167.60.12", "10.167.60.13")

    flow_specs = {c[3] for c in calls}
    assert any("nw_src=10.167.60.12,nw_dst=10.167.60.13" in f for f in flow_specs)
    assert any("nw_src=10.167.60.13,nw_dst=10.167.60.12" in f for f in flow_specs)


def test_revoke_ip_flows_clears_both_directions(monkeypatch):
    """座位釋放、IP 要被下一個座位撿走前，舊的允許規則要先清乾淨——不清的話
    兩個不相干的座位會透過殘留規則意外連得到彼此（v1 抓到的第二個真實 bug）。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "scripts.range.seat_provisioner.subprocess.run",
        lambda args, **kw: (calls.append(args), _FakeCompleted(""))[1],
    )

    _revoke_ip_flows("br-range", "10.167.60.12")

    del_calls = [c for c in calls if c[:2] == ["ovs-ofctl", "del-flows"]]
    assert len(del_calls) == 2
    assert any("nw_src=10.167.60.12" in c[3] for c in del_calls)
    assert any("nw_dst=10.167.60.12" in c[3] for c in del_calls)


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
