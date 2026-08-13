"""Response 命令佇列（票 09）。

receiver 把封鎖需求**放進佇列**（在 Z-MGMT 內完成，不連向 target）。
target 側的 agent 之後**主動拉取**這個佇列 —— 於是全程沒有任何連入 target 的連線，
`TARGET → MGMT` 單向不被破壞。這就是票 09 用 agent pull 取代 03 直寫的理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol


@dataclass(frozen=True)
class ResponseCommand:
    """Target agent 可執行的命令。

    `source_ip` 與其餘語意一律由已治理的 attack Core Event 複製；agent 不查 Loki、
    不猜 service 名稱，也不自行判定該封誰。
    """

    event_id: str
    source_ip: str
    exercise_id: str
    scenario_id: str
    severity: str
    technique: str
    service: str
    action: str = "block"
    # "manual"（藍隊動作觸發，#51）或 "auto"（測試載具，#48）。
    # 計分（#33）只認 "manual"——這個欄位是唯一的判準，不得另外猜。
    triggered_by: str = "manual"

    @classmethod
    def from_core_event(cls, event: dict[str, Any], *, triggered_by: str = "manual") -> "ResponseCommand":
        target = event.get("target") or {}
        source_ip = target.get("source_ip")
        if not source_ip:
            raise ValueError("attack Core Event target.source_ip is required for response")
        return cls(
            event_id=event["event_id"],
            source_ip=source_ip,
            exercise_id=event["exercise_id"],
            scenario_id=event["scenario_id"],
            severity=event["severity"],
            technique=event["technique"],
            service=target["service"],
            triggered_by=triggered_by,
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "source_ip": self.source_ip,
            "exercise_id": self.exercise_id,
            "scenario_id": self.scenario_id,
            "severity": self.severity,
            "technique": self.technique,
            "service": self.service,
            "action": self.action,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ResponseCommand":
        return cls(**{name: raw[name] for name in cls.__dataclass_fields__})


class CommandQueue(Protocol):
    def enqueue(self, command: ResponseCommand) -> None: ...
    def claim(self) -> list[ResponseCommand]: ...
    def complete(self, command: ResponseCommand) -> None: ...


@dataclass
class InMemoryCommandQueue:
    """佇列的參考實作。正式版住 Z-MGMT，agent 以 pull 存取（見 agent.py）。"""

    _pending: list[ResponseCommand] = field(default_factory=list)
    _done: list[ResponseCommand] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def enqueue(self, command: ResponseCommand) -> None:
        with self._lock:
            self._pending.append(command)

    def claim(self) -> list[ResponseCommand]:
        with self._lock:
            claimed = list(self._pending)
            self._pending.clear()
            return claimed

    def complete(self, command: ResponseCommand) -> None:
        with self._lock:
            self._done.append(command)

    @property
    def done(self) -> list[ResponseCommand]:
        with self._lock:
            return list(self._done)
