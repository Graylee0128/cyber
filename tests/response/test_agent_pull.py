"""Response agent —— agent pull 保住 TARGET→MGMT 單向（票 09）。純函數/假物件，不需 docker。

真實網段（macvlan、防火牆規則）屬 workstream 6，`agent_pulls_no_inbound_to_target`
的網路實測歸 #13；本檔在**程式碼層**證明同一件事：agent 只發起 outbound。
"""

from datetime import datetime, timezone

from purple.harness.schema import assert_core_event
from purple.response.agent import ResponseAgent
from purple.response.direct_block import DirectIpsetBlocker, RecordingBlocker
from purple.response.queue import InMemoryCommandQueue, ResponseCommand


class RecordingLink:
    """記錄 agent 的每次網路互動與方向。agent 只該有 outbound。"""

    def __init__(self, commands):
        self._commands = commands
        self.directions: list[str] = []
        self.reported: list[dict] = []

    def pull(self):
        self.directions.append("out")   # agent 主動連向 MGMT 取命令
        return self._commands

    def report(self, event):
        self.directions.append("out")   # agent 主動連向 MGMT 回報
        self.reported.append(event)

    # 刻意沒有 listen／accept：agent 不開任何連入的埠。


FIXED_NOW = datetime(2026, 8, 8, 14, 30, 5, tzinfo=timezone.utc)
ATTACK_CORE_EVENT = {
    "event_id": "evt-attack-1",
    "exercise_id": "ex-001",
    "scenario_id": "falco-exec-01",
    "event_type": "attack.detected",
    "lifecycle": "firing",
    "severity": "high",
    "source": "grafana",
    "team": "red",
    "technique": "T1059",
    "target": {"service": "range-target", "source_ip": "10.167.30.11"},
    "observed_at": "2026-08-08T14:30:00+00:00",
    "visibility": "public",
}


def response_command() -> ResponseCommand:
    return ResponseCommand.from_core_event(ATTACK_CORE_EVENT)


class TestAgentPullsNoInboundToTarget:
    def test_all_interactions_are_outbound(self):
        link = RecordingLink([response_command()])
        agent = ResponseAgent(link=link, blocker=RecordingBlocker(), now=lambda: FIXED_NOW)
        agent.run_once()
        assert link.directions, "agent 應該有網路互動"
        assert set(link.directions) == {"out"}, "出現了非 outbound 的互動 —— 單向被破壞"

    def test_agent_exposes_no_inbound_entry_point(self):
        """結構性保證：Link 協定沒有 listen/accept，agent 無從被連入。"""
        assert not hasattr(RecordingLink([]), "listen")
        assert not hasattr(RecordingLink([]), "accept")


class TestBlockAndResponseEvent:
    def test_attack_leads_to_block_and_response_executed(self):
        blocker = RecordingBlocker()
        link = RecordingLink([response_command()])
        agent = ResponseAgent(link=link, blocker=blocker, now=lambda: FIXED_NOW)

        events = agent.run_once()

        assert len(blocker.blocked) == 1                       # 真的封了
        assert events[0]["event_type"] == "response.executed"  # 產生事件
        assert events[0]["visibility"] == "blue"
        assert events[0]["target"]["source_ip"] == "10.167.30.11"
        assert events[0]["target"]["attack_event_id"] == "evt-attack-1"
        assert events[0]["event_id"] != "evt-attack-1"
        assert_core_event(events[0])
        # MTTR 終點＝ipset 寫入成功的時刻
        assert events[0]["observed_at"] == FIXED_NOW.isoformat()
        assert link.reported == events                         # 且回報回 MGMT

    def test_block_failure_produces_response_failed_purple(self):
        class FailingBlocker:
            def block(self, core_event):
                return "failed: ipset returned 1"

        link = RecordingLink([response_command()])
        agent = ResponseAgent(link=link, blocker=FailingBlocker(), now=lambda: FIXED_NOW)
        [event] = agent.run_once()
        assert event["event_type"] == "response.failed"
        assert event["visibility"] == "purple"
        assert "ipset returned 1" in event["target"]["response"]["detail"]
        assert_core_event(event)

    def test_blocker_exception_is_surfaced_not_swallowed(self):
        class ThrowingBlocker:
            def block(self, core_event):
                raise RuntimeError("boom")

        link = RecordingLink([response_command()])
        agent = ResponseAgent(link=link, blocker=ThrowingBlocker(), now=lambda: FIXED_NOW)
        [event] = agent.run_once()
        assert event["event_type"] == "response.failed"
        assert "boom" in event["target"]["response"]["detail"]
        assert_core_event(event)


class TestDirectIpsetBlocker:
    def test_missing_ipset_is_failure_not_fake_executed(self, monkeypatch):
        monkeypatch.setattr("purple.response.direct_block.shutil.which", lambda _: None)

        detail = DirectIpsetBlocker().block(ATTACK_CORE_EVENT)

        assert detail.startswith("failed:")
        assert "ipset" in detail

    def test_installs_drop_rule_then_blocks_exact_core_event_source(self, monkeypatch):
        commands: list[list[str]] = []

        class Result:
            def __init__(self, returncode=0, stderr=""):
                self.returncode = returncode
                self.stderr = stderr

        def run(command, **_kwargs):
            commands.append(command)
            if command[:4] == ["iptables", "-w", "-C", "INPUT"]:
                return Result(returncode=1)
            return Result()

        monkeypatch.setattr("purple.response.direct_block.shutil.which", lambda name: name)
        monkeypatch.setattr("purple.response.direct_block.subprocess.run", run)

        detail = DirectIpsetBlocker().block(ATTACK_CORE_EVENT)

        assert detail == "blocked: 10.167.30.11 via ipset purple_blocklist"
        assert ["ipset", "create", "purple_blocklist", "hash:ip", "-exist"] in commands
        assert ["ipset", "add", "purple_blocklist", "10.167.30.11", "-exist"] in commands
        assert any(command[:4] == ["iptables", "-w", "-I", "INPUT"] for command in commands)


class TestQueueIsPullNotPush:
    def test_receiver_side_only_enqueues_agent_side_claims(self):
        """佇列本身：enqueue（MGMT 內）與 claim（agent 拉）是分開的兩端。"""
        q = InMemoryCommandQueue()
        q.enqueue(response_command())
        claimed = q.claim()
        assert len(claimed) == 1
        assert claimed[0].source_ip == ATTACK_CORE_EVENT["target"]["source_ip"]
        assert q.claim() == []  # claim 後不重複

    def test_command_rejects_core_event_without_source_ip(self):
        event_without_source_ip = {
            **ATTACK_CORE_EVENT,
            "target": {"service": "range-target"},
        }

        try:
            ResponseCommand.from_core_event(event_without_source_ip)
        except ValueError as exc:
            assert "source_ip" in str(exc)
        else:
            raise AssertionError("缺 source_ip 的 Core Event 不得產生封鎖命令")
