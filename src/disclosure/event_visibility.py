from __future__ import annotations

#: spec §2.4：visibility 由 event_type 決定，Grafana rule 不得覆寫。
VISIBILITY_BY_EVENT_TYPE = {
    "attack.detected": "public",
    "detection.hit": "blue",
    "detection.miss": "purple",
    "response.executed": "blue",
    "response.failed": "purple",
}

#: Core Events that represent a detection starting point. Shared by P2
#: coverage and WS5 Blue reaction scoring so a response/exercise event cannot
#: be counted as a detection by one consumer but not the other.
DETECTION_EVENT_TYPES = ("attack.detected", "detection.hit")
#: `exercise.*` 一律 instructor。
EXERCISE_PREFIX = "exercise."
EXERCISE_VISIBILITY = "instructor"


def expected_visibility(event_type: str) -> str | None:
    if event_type.startswith(EXERCISE_PREFIX):
        return EXERCISE_VISIBILITY
    return VISIBILITY_BY_EVENT_TYPE.get(event_type)
