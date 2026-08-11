"""Target agent 的 outbound-only HTTP transport。"""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from purple.response.queue import ResponseCommand


class HttpLink:
    """只提供 GET pull 與 POST report；刻意沒有任何 server/listen/accept。"""

    def __init__(self, base_url: str, timeout_s: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def pull(self) -> list[ResponseCommand]:
        with urlopen(  # noqa: S310 - URL 由受控的 systemd EnvironmentFile 提供
            f"{self.base_url}/response/commands",
            timeout=self.timeout_s,
        ) as response:
            payload = json.load(response)
        return [ResponseCommand.from_dict(item) for item in payload["commands"]]

    def report(self, event: dict) -> None:
        body = json.dumps(event).encode()
        request = Request(
            f"{self.base_url}/response/report",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
            response.read()
