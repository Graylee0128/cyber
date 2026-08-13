from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class HttpRangePublisher:
    """Idempotent HTTP boundary to Range Core's Admission-owned roster API."""

    def __init__(self, base_url: str, token: str, *, timeout_seconds: float = 3):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def publish_player(self, *, exercise_id: str, player_id: str, team: str,
                       source_ip: str | None) -> None:
        body = {"team": team}
        if source_ip is not None:
            body["source_ip"] = source_ip
        self._request("PUT", f"/api/exercises/{exercise_id}/players/{player_id}", body)

    def revoke_player(self, exercise_id: str, player_id: str) -> None:
        self._request("DELETE", f"/api/exercises/{exercise_id}/players/{player_id}")

    def _request(self, method: str, path: str, body: dict | None = None) -> None:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            self.base_url + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status not in (200, 201, 204):
                    raise RuntimeError(f"Range Core returned {response.status}")
        except HTTPError as exc:
            if method == "DELETE" and exc.code == 404:
                return
            raise
