"""Target 側 response agent（票 09）。

agent 只做一件事，而且只用一個方向的連線：**主動拉取** MGMT 的命令佇列、
在本機執行封鎖、把結果**回報**回 MGMT。全程由 agent 發起連線（outbound），
沒有任何連入 target 的 socket —— 這是 `TARGET → MGMT` 單向的保證。

MTTR 的終點是 **ipset 寫入成功**（response.executed），不是 Grafana Resolved（ADR ⑦）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol
import uuid

from purple.harness.schema import assert_core_event, expected_visibility
from purple.response.direct_block import Blocker, DirectIpsetBlocker
from purple.response.queue import ResponseCommand


class Link(Protocol):
    """agent 對外的網路能力，**只有 outbound**。

    刻意沒有 listen／accept：agent 不開任何連入的埠。命令用 pull 取得，
    結果用 report 送出，兩者都是 agent 主動發起。
    """

    def pull(self) -> list[ResponseCommand]: ...
    def report(self, event: dict) -> None: ...


@dataclass
class ResponseAgent:
    link: Link
    blocker: Blocker = field(default_factory=DirectIpsetBlocker)
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def run_once(self) -> list[dict]:
        """拉取一批命令、逐一執行、回報。回傳產生的 response 事件。"""
        events: list[dict] = []
        for command in self.link.pull():
            events.append(self._execute(command))
        for event in events:
            self.link.report(event)
        return events

    def _execute(self, command: ResponseCommand) -> dict:
        core_event = {
            "event_id": command.event_id,
            "target": {"service": command.service, "source_ip": command.source_ip},
        }
        try:
            detail = self.blocker.block(core_event)
        except Exception as exc:  # 封鎖失敗要現形，不可靜默
            return self._event(command, "response.failed", "purple", str(exc))

        if detail.startswith("failed"):
            return self._event(command, "response.failed", "purple", detail)
        # response.executed 的時刻＝ipset 寫入成功的時刻，即 MTTR 終點。
        return self._event(command, "response.executed", "blue", detail)

    def _event(self, command: ResponseCommand, event_type: str, visibility: str, detail: str) -> dict:
        observed_at = self.now().isoformat()
        event = {
            # response 是另一個 domain event，不能與 attack 的 (event_id, firing) 撞主鍵。
            # 因果關係放在 target.attack_event_id，仍可從 response 回溯原攻擊。
            "event_id": "evt-" + uuid.uuid4().hex,
            "exercise_id": command.exercise_id,
            "scenario_id": command.scenario_id,
            "event_type": event_type,
            "lifecycle": "firing",
            "severity": command.severity,
            "source": "response-agent",
            "team": "blue",
            "technique": command.technique,
            "target": {
                "service": command.service,
                "source_ip": command.source_ip,
                "attack_event_id": command.event_id,
                "response": {"action": command.action, "detail": detail},
            },
            # response.executed 的 observed_at 是 ipset 成功回傳後才取值，正是 ADR ⑦ 的 MTTR 終點。
            "observed_at": observed_at,
            "visibility": visibility,
        }
        assert visibility == expected_visibility(event_type)
        assert_core_event(event)
        return event
