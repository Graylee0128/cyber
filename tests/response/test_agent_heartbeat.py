"""Response agent heartbeat is emitted after a successful outbound pull."""

import json
from datetime import datetime, timezone

import pytest

from purple.response.agent import JsonlHeartbeat, ResponseAgent
from purple.response.direct_block import RecordingBlocker


NOW = datetime(2026, 8, 11, 6, 30, tzinfo=timezone.utc)


class EmptyLink:
    def pull(self):
        return []

    def report(self, _event):
        raise AssertionError("empty pull must not report response event")


def test_successful_empty_pull_emits_response_agent_heartbeat(tmp_path):
    path = tmp_path / "response-agent.jsonl"
    agent = ResponseAgent(
        link=EmptyLink(),
        blocker=RecordingBlocker(),
        now=lambda: NOW,
        heartbeat=JsonlHeartbeat(path),
    )

    assert agent.run_once() == []

    [line] = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {
        "source": "response-agent",
        "observed_at": NOW.isoformat(),
    }


def test_failed_pull_does_not_claim_agent_is_healthy(tmp_path):
    class BrokenLink:
        def pull(self):
            raise OSError("mgmt unreachable")

        def report(self, _event):
            pass

    path = tmp_path / "response-agent.jsonl"
    agent = ResponseAgent(
        link=BrokenLink(),
        blocker=RecordingBlocker(),
        now=lambda: NOW,
        heartbeat=JsonlHeartbeat(path),
    )

    with pytest.raises(OSError, match="unreachable"):
        agent.run_once()

    assert not path.exists()


def test_heartbeat_file_is_throttled_to_the_30_second_contract(tmp_path):
    path = tmp_path / "response-agent.jsonl"
    writer = JsonlHeartbeat(path)

    writer(NOW)
    writer(NOW.replace(second=10))
    writer(NOW.replace(second=30))

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[-1])["observed_at"] == NOW.replace(second=30).isoformat()
