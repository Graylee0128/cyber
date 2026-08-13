"""Target 側 response agent（票 09）。

agent 只做一件事，而且只用一個方向的連線：**主動拉取** MGMT 的命令佇列、
在本機執行封鎖、把結果**回報**回 MGMT。全程由 agent 發起連線（outbound），
沒有任何連入 target 的 socket —— 這是 `TARGET → MGMT` 單向的保證。

MTTR 的終點是 **ipset 寫入成功**（response.executed），不是 Grafana Resolved（ADR ⑦）。
"""

from __future__ import annotations

import json
from pathlib import Path
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
class JsonlHeartbeat:
    """把成功 pull 寫進既有 Alloy→Loki log transport，不新增 endpoint。"""

    path: Path | str = "/var/log/purplescope/response-agent.jsonl"
    source: str = "response-agent"
    interval_s: float = 30
    _last_written: datetime | None = field(default=None, init=False, repr=False)

    def __call__(self, observed_at: datetime) -> None:
        if observed_at.tzinfo is None or observed_at.tzinfo.utcoffset(observed_at) is None:
            raise ValueError("response agent heartbeat 缺少 timezone")
        if self._last_written is not None:
            elapsed = (observed_at - self._last_written).total_seconds()
            if elapsed < 0:
                raise ValueError("response agent heartbeat 時間倒退")
            if elapsed < self.interval_s:
                return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"source": self.source, "observed_at": observed_at.isoformat()}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._last_written = observed_at


@dataclass
class ResponseAgent:
    link: Link
    blocker: Blocker = field(default_factory=DirectIpsetBlocker)
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    heartbeat: Callable[[datetime], None] | None = None

    def run_once(self) -> list[dict]:
        """拉取一批命令、逐一執行、回報。回傳產生的 response 事件。"""
        commands = self.link.pull()
        events: list[dict] = []
        for command in commands:
            events.append(self._execute(command))
        for event in events:
            self.link.report(event)
        # 成功完成 outbound pull 才算 agent 路徑健康；放在 response 之後，heartbeat
        # 寫檔故障也不會阻止已取得的封鎖命令執行／回報。
        if self.heartbeat is not None:
            self.heartbeat(self.now())
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
