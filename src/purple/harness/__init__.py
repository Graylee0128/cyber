"""測試載具的公開 API。

契約測試（02b 起）只該從這裡 import，不要直接摸內部模組 ——
內部可以重構，這層的名字是承諾。
"""

from purple.harness.attacker import (
    AttackFailed,
    AttackResult,
    inject_sqli,
    make_marker,
    trigger_exec,
)
from purple.harness.loki_probe import loki_has, loki_line_count
from purple.harness.schema import SchemaViolation, assert_core_event
from purple.harness.waiting import EventNotSeen, wait_for_event

__all__ = [
    "assert_core_event",
    "SchemaViolation",
    "wait_for_event",
    "EventNotSeen",
    "inject_sqli",
    "make_marker",
    "trigger_exec",
    "loki_has",
    "loki_line_count",
    "AttackResult",
    "AttackFailed",
]
