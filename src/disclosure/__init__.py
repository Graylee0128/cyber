"""跨 context 的揭露契約。purple 與 range_core 都 import 這裡，但仍不互相 import。"""

from disclosure.clearance import (
    CALLER_CLEARANCE,
    VISIBILITY_RANK,
    visibility_rank,
)
from disclosure.event_visibility import (
    EXERCISE_PREFIX,
    EXERCISE_VISIBILITY,
    VISIBILITY_BY_EVENT_TYPE,
    expected_visibility,
)
from disclosure.fields import FIELD_MASKING
from disclosure.identity import (
    BEARER_PREFIX,
    TOKEN_HEADER,
    extract_token,
    load_service_tokens,
    resolve_identity,
)

__all__ = [
    "BEARER_PREFIX",
    "CALLER_CLEARANCE",
    "EXERCISE_PREFIX",
    "EXERCISE_VISIBILITY",
    "FIELD_MASKING",
    "TOKEN_HEADER",
    "VISIBILITY_BY_EVENT_TYPE",
    "VISIBILITY_RANK",
    "expected_visibility",
    "extract_token",
    "load_service_tokens",
    "resolve_identity",
    "visibility_rank",
]
