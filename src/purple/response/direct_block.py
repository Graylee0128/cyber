"""target 本機的 ipset 封鎖執行器（票 #17）。

receiver 只排命令；target agent 主動 pull 後在本機呼叫本模組，因此不需要
MGMT 反向連入 target。封鎖來源只讀 Core Event 的 `target.source_ip`，不自行判斷。
只有 ipset 寫入與 INPUT drop rule 都成功才回報成功；缺工具或任一步失敗都回
`failed:`，避免把 no-op 誤記成 response.executed（ADR ⑦）。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_SET = "purple_blocklist"


class Blocker(Protocol):
    def block(self, core_event: dict[str, Any]) -> str:
        """封鎖事件對應的來源，回傳實際採取的動作描述。"""
        ...


@dataclass
class DirectIpsetBlocker:
    """用 ipset + iptables 封鎖來源；兩者成功才算 response 生效。"""

    ipset_set: str = DEFAULT_SET
    attempts: list[str] = field(default_factory=list)

    def block(self, core_event: dict[str, Any]) -> str:
        source_ip = _source_ip(core_event)
        self.attempts.append(source_ip)

        missing = [name for name in ("ipset", "iptables") if shutil.which(name) is None]
        if missing:
            return f"failed: required command unavailable: {', '.join(missing)}"

        created = _run(["ipset", "create", self.ipset_set, "hash:ip", "-exist"])
        if created.returncode != 0:
            return f"failed: ipset create: {created.stderr.strip()}"

        rule = [
            "INPUT", "-p", "tcp", "--dport", "80", "-m", "set",
            "--match-set", self.ipset_set, "src", "-j", "DROP",
        ]
        checked = _run(["iptables", "-w", "-C", *rule])
        if checked.returncode != 0:
            inserted = _run(["iptables", "-w", "-I", *rule])
            if inserted.returncode != 0:
                return f"failed: iptables insert: {inserted.stderr.strip()}"

        result = _run(
            ["ipset", "add", self.ipset_set, source_ip, "-exist"],
        )
        if result.returncode != 0:
            return f"failed: ipset add: {result.stderr.strip()}"
        return f"blocked: {source_ip} via ipset {self.ipset_set}"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
    )


@dataclass
class RecordingBlocker:
    """測試用：只記錄被要求封鎖的事件，不碰系統。"""

    blocked: list[dict[str, Any]] = field(default_factory=list)

    def block(self, core_event: dict[str, Any]) -> str:
        self.blocked.append(core_event)
        return f"recorded block for {core_event.get('event_id')}"


def _source_ip(core_event: dict[str, Any]) -> str:
    target = core_event.get("target", {})
    source_ip = target.get("source_ip")
    if not source_ip:
        raise ValueError("Core Event target.source_ip is required for blocking")
    return source_ip
