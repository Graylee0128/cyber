"""Core Event 契約的可執行版本。

契約在 `.scratch/p1-output-contract/spec.md` §2。散文會被繞過，斷言不會 ——
`evidence_ref` 之所以不會在某次 PR 裡悄悄長回來，靠的是這裡。
"""

from __future__ import annotations

import json
from datetime import datetime

REQUIRED_FIELDS = frozenset(
    {
        "event_id",
        "exercise_id",
        "scenario_id",
        "event_type",
        "lifecycle",
        "severity",
        "source",
        "team",
        "technique",
        "target",
        "observed_at",
        "visibility",
    }
)

#: 明確禁用的欄位（spec §2.1）。單獨列出是為了給出比「未知欄位」更好的錯誤訊息。
FORBIDDEN_FIELDS = frozenset({"evidence_ref", "raw", "payload", "threshold", "query", "backend"})

#: 後端詞彙。出現在任何值裡都代表遙測細節漏進了遊戲語意層。
#: 刻意只列 spec §2.1 明寫的三個 —— "prometheus" 沒列，因為票 10 的 metric 路徑
#: 可能合法地把它當成來源名，多擋一個字會擋掉還沒發生的正當用法。
BACKEND_WORDS = ("loki", "logql", "promql")

LIFECYCLES = frozenset({"firing", "resolved"})

#: spec §2.4：visibility 由 event_type 決定，Grafana rule 不得覆寫。
VISIBILITY_BY_EVENT_TYPE = {
    "attack.detected": "public",
    "detection.hit": "blue",
    "detection.miss": "purple",
    "response.executed": "blue",
    "response.failed": "purple",
}
#: `exercise.*` 一律 instructor。
EXERCISE_PREFIX = "exercise."
EXERCISE_VISIBILITY = "instructor"


class SchemaViolation(AssertionError):
    """事件不符合 Core Event 契約。繼承 AssertionError，讓 pytest 直接當成斷言失敗。"""


def expected_visibility(event_type: str) -> str | None:
    if event_type.startswith(EXERCISE_PREFIX):
        return EXERCISE_VISIBILITY
    return VISIBILITY_BY_EVENT_TYPE.get(event_type)


def assert_core_event(event: dict) -> None:
    """符合契約就靜靜通過，不符合就拋出指名問題的 SchemaViolation。"""
    _reject_forbidden(event)
    _require_exact_fields(event)
    _check_enums(event)
    _check_visibility(event)
    _check_observed_at(event["observed_at"])
    _reject_backend_vocabulary(event)


def _reject_forbidden(event: dict) -> None:
    for field in sorted(FORBIDDEN_FIELDS & event.keys()):
        raise SchemaViolation(
            f"forbidden field {field!r}: 遙測細節不進 Core Event（spec §2.1 / ADR ④）"
        )


def _require_exact_fields(event: dict) -> None:
    missing = REQUIRED_FIELDS - event.keys()
    if missing:
        raise SchemaViolation(f"missing required field(s): {', '.join(sorted(missing))}")

    unknown = event.keys() - REQUIRED_FIELDS
    if unknown:
        raise SchemaViolation(
            f"unknown field(s): {', '.join(sorted(unknown))} —— "
            f"契約是完整清單，不是最低要求"
        )


def _check_enums(event: dict) -> None:
    lifecycle = event["lifecycle"]
    if lifecycle not in LIFECYCLES:
        extra = "（Grafana 內部狀態，不是遊戲語意，spec §2.2）" if lifecycle == "pending" else ""
        raise SchemaViolation(f"invalid lifecycle {lifecycle!r}{extra}")

    if expected_visibility(event["event_type"]) is None:
        raise SchemaViolation(f"unknown event_type {event['event_type']!r}")


def _check_visibility(event: dict) -> None:
    expected = expected_visibility(event["event_type"])
    actual = event["visibility"]
    if actual != expected:
        raise SchemaViolation(
            f"visibility {actual!r} does not match event_type {event['event_type']!r} "
            f"(expected {expected!r}) —— visibility 由 receiver 依對照表決定，"
            f"rule 不得覆寫（spec §2.4）"
        )


def _check_observed_at(value: object) -> None:
    if not isinstance(value, str):
        raise SchemaViolation(f"observed_at must be an ISO-8601 string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SchemaViolation(f"observed_at {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise SchemaViolation(f"observed_at {value!r} has no timezone")


def _reject_backend_vocabulary(event: dict) -> None:
    blob = json.dumps(event, ensure_ascii=False).lower()
    for word in BACKEND_WORDS:
        if word in blob:
            raise SchemaViolation(
                f"backend vocabulary {word!r} appears in the event —— "
                f"換掉後端時 Core Event Schema 一個字都不該動（spec §2.1）"
            )
