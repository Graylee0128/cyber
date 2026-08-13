from __future__ import annotations

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


def expected_visibility(event_type: str) -> str | None:
    if event_type.startswith(EXERCISE_PREFIX):
        return EXERCISE_VISIBILITY
    return VISIBILITY_BY_EVENT_TYPE.get(event_type)
