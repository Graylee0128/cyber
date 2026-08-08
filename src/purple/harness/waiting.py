"""等事件出現。

管路是非同步的：攻擊打完，事件要經過 Alloy → Loki → Grafana → receiver 才落地。
測試不能立刻斷言，也不能 `sleep(30)` 了事。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_POLL_S = 0.5

Fetch = Callable[[], list[dict[str, Any]]]
Match = Callable[[dict[str, Any]], bool]


class EventNotSeen(AssertionError):
    """等到逾時仍沒有符合條件的事件。訊息一定要說出「看到了什麼」。"""


def wait_for_event(
    fetch: Fetch,
    match: Match,
    what: str = "matching event",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_s: float = DEFAULT_POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """輪詢到出現符合的事件為止，逾時則拋出 EventNotSeen。

    逾時訊息會列出期間看到的所有事件。只說「逾時」等於把人丟回去手動查 ——
    而那時候管路狀態已經變了。
    """
    deadline = clock() + timeout_s
    seen: list[dict[str, Any]] = []

    while True:
        seen = fetch()
        for event in seen:
            if match(event):
                return event

        if clock() >= deadline:
            raise EventNotSeen(_timeout_message(what, timeout_s, seen))

        sleep(poll_s)


def _timeout_message(what: str, timeout_s: float, seen: list[dict[str, Any]]) -> str:
    head = f"等了 {timeout_s:g}s 沒有出現 {what}。"
    if not seen:
        return head + "\n期間**完全沒有**任何事件 —— 管路可能整條沒通，不只是這一筆沒中。"

    lines = [f"{head}\n期間看到 {len(seen)} 筆事件："]
    for event in seen[:10]:
        lines.append("  - " + _summarise(event))
    if len(seen) > 10:
        lines.append(f"  … 另有 {len(seen) - 10} 筆")
    return "\n".join(lines)


def _summarise(event: dict[str, Any]) -> str:
    """挑出足以辨認的欄位。整包 dump 會把逾時訊息淹掉。"""
    keys = ("event_id", "event_type", "lifecycle", "scenario_id", "technique", "observed_at")
    parts = [f"{k}={event[k]!r}" for k in keys if k in event]
    return ", ".join(parts) if parts else json.dumps(event, ensure_ascii=False)[:120]
