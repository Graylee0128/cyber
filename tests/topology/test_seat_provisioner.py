"""#62 Seat Provisioner Agent —— 純函式邏輯（輪詢/回報/孤兒回收）用假 client／
builder 測，不碰真的 docker/OVS（那段見 #62 PR 的 T3/T4 證據，本檔只測 T1）。
"""

from __future__ import annotations

import pytest

from scripts.range.seat_provisioner import ProvisionerConfig, run, sweep_orphans


class FakeAdmission:
    def __init__(self, pending_seats=None, active_ids=None):
        self._pending = list(pending_seats or [])
        self._active = set(active_ids or [])
        self.marked_ready: list[tuple[str, list[dict]]] = []

    def pending(self, team):
        assert team == "red"
        return self._pending

    def active_seat_ids(self, team):
        assert team == "red"
        return self._active

    def mark_ready(self, seat_id, endpoints):
        self.marked_ready.append((seat_id, endpoints))
        return True


def _config(**overrides):
    defaults = dict(
        admission_url="http://admission", admission_token="tok",
        poll_seconds=0, once=True, red_image="purplescope/red-attacker:latest",
    )
    defaults.update(overrides)
    return ProvisionerConfig(**defaults)


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


def test_pending_seat_gets_built_and_marked_ready(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: {})
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client: 0)
    admission = FakeAdmission(pending_seats=[{"seat_id": "s1", "exercise_id": "EX", "team": "red", "kind": "shell"}])
    built = {"endpoints": [{"terminal": "main", "host": "10.167.30.11", "port": 7681}]}

    run(_config(), client=admission, builder=lambda seat_id, **_: built)

    assert admission.marked_ready == [("s1", built["endpoints"])]


def test_builder_failure_is_skipped_not_fatal(monkeypatch):
    """見檔頭：建置失敗留給 admission 既有的逾時重試處理，provisioner 只要不當掉。"""
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: {})
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client: 0)
    admission = FakeAdmission(pending_seats=[{"seat_id": "s1", "exercise_id": "EX", "team": "red", "kind": "shell"}])

    def failing_builder(seat_id, **_):
        raise RuntimeError("docker daemon unreachable")

    run(_config(), client=admission, builder=failing_builder)  # 不應該拋例外

    assert admission.marked_ready == []


def test_poll_failure_does_not_crash_the_loop(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: {})
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client: 0)

    class BrokenAdmission(FakeAdmission):
        def pending(self, team):
            raise ConnectionError("admission unreachable")

    run(_config(), client=BrokenAdmission(), builder=lambda seat_id, **_: {"endpoints": []})
    # 沒拋例外就是通過；once=True 讓迴圈跑一輪後正常返回。


def test_once_mode_does_not_sleep(monkeypatch):
    monkeypatch.setattr("scripts.range.seat_provisioner.load_zones", lambda: {})
    monkeypatch.setattr("scripts.range.seat_provisioner.sweep_orphans", lambda client: 0)
    calls = []

    run(
        _config(once=True), client=FakeAdmission(),
        builder=lambda seat_id, **_: {"endpoints": []}, sleep=lambda s: calls.append(s),
    )

    assert calls == []


def test_sweep_orphans_only_removes_containers_not_in_active_set(monkeypatch):
    """核心正確性：座位已 ready（active）不能被當孤兒拆掉，只有 released/failed
    （不在 active 名單）的容器才拆。這是這支 agent 最容易寫錯、後果最重的一段——
    v1 曾經誤用「不在 pending 名單」當孤兒判準，會在每次重啟時把剛建好、正在
    服務玩家的座位一起砍掉，寫測試時才抓到，見 PR 說明。"""
    calls: list[list[str]] = []

    def fake_run(args, **kw):
        calls.append(args)
        if args[:2] == ["docker", "ps"]:
            return _FakeCompleted("seat-red-still-active\nseat-red-long-gone\n")
        return _FakeCompleted("")

    monkeypatch.setattr("scripts.range.seat_provisioner.subprocess.run", fake_run)
    admission = FakeAdmission(active_ids={"still-active"})

    removed = sweep_orphans(admission)

    assert removed == 1
    rm_calls = [c for c in calls if c[:2] == ["docker", "rm"]]
    assert rm_calls == [["docker", "rm", "-f", "seat-red-long-gone"]]


class _FakeCompleted:
    def __init__(self, stdout: str):
        self.stdout = stdout
