from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime


def _seconds(now: datetime | None) -> int:
    return int(now.timestamp()) if now is not None else int(time.time())


def onsite_code(secret: str, exercise_id: str, now: datetime | None = None) -> str:
    bucket = _seconds(now) // 60
    message = f"{exercise_id}:{bucket}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:12]


def verify_onsite_code(
    supplied: str, secret: str, exercise_id: str, now: datetime | None = None
) -> bool:
    if not supplied or not secret:
        return False
    return hmac.compare_digest(supplied, onsite_code(secret, exercise_id, now))
