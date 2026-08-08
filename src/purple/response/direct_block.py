"""ipset 直寫封鎖 —— 票 03 的 expand 基線。

這是攻擊 → 封鎖的最短路徑：receiver 收到 attack.detected 就直接寫 ipset。
它會破壞 `TARGET → MGMT` 單向（管理平面反向連入 target 才能寫），
所以**票 09 會用 agent pull 取代它**（expand–contract 的 contract 階段）。

在那之前它必須可用 —— 沒有它，03 到 09 之間就沒有任何封鎖能力。
本機／CI 沒有 ipset，落到 no-op 並記錄意圖，行為仍可被契約測試觀察。
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
    """直接呼叫 ipset。ipset 不存在時 no-op 但記錄意圖，不假裝成功也不炸。"""

    ipset_set: str = DEFAULT_SET
    attempts: list[str] = field(default_factory=list)

    def block(self, core_event: dict[str, Any]) -> str:
        source_ip = _source_ip(core_event)
        self.attempts.append(source_ip)

        if shutil.which("ipset") is None:
            return f"noop: ipset unavailable, would block {source_ip}"

        result = subprocess.run(
            ["ipset", "add", self.ipset_set, source_ip, "-exist"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return f"failed: {result.stderr.strip()}"
        return f"blocked: {source_ip} via ipset {self.ipset_set}"


@dataclass
class RecordingBlocker:
    """測試用：只記錄被要求封鎖的事件，不碰系統。"""

    blocked: list[dict[str, Any]] = field(default_factory=list)

    def block(self, core_event: dict[str, Any]) -> str:
        self.blocked.append(core_event)
        return f"recorded block for {core_event.get('event_id')}"


def _source_ip(core_event: dict[str, Any]) -> str:
    target = core_event.get("target", {})
    return target.get("source_ip") or target.get("service", "unknown")
