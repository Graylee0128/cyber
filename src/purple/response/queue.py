"""Response 命令佇列（票 09）。

receiver 把封鎖需求**放進佇列**（在 Z-MGMT 內完成，不連向 target）。
target 側的 agent 之後**主動拉取**這個佇列 —— 於是全程沒有任何連入 target 的連線，
`TARGET → MGMT` 單向不被破壞。這就是票 09 用 agent pull 取代 03 直寫的理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ResponseCommand:
    event_id: str
    source_ip: str
    action: str = "block"


class CommandQueue(Protocol):
    def enqueue(self, command: ResponseCommand) -> None: ...
    def claim(self) -> list[ResponseCommand]: ...
    def complete(self, command: ResponseCommand) -> None: ...


@dataclass
class InMemoryCommandQueue:
    """佇列的參考實作。正式版住 Z-MGMT，agent 以 pull 存取（見 agent.py）。"""

    _pending: list[ResponseCommand] = field(default_factory=list)
    _done: list[ResponseCommand] = field(default_factory=list)

    def enqueue(self, command: ResponseCommand) -> None:
        self._pending.append(command)

    def claim(self) -> list[ResponseCommand]:
        claimed = list(self._pending)
        self._pending.clear()
        return claimed

    def complete(self, command: ResponseCommand) -> None:
        self._done.append(command)

    @property
    def done(self) -> list[ResponseCommand]:
        return list(self._done)
