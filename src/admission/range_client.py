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

    def prepare(self, scenario_id: str) -> dict:
        """反代教官呼叫 Range Core 的 prepare（#143 項目 1）——Admission 本來就
        持有 `admission` 服務身分的 token，`require_identity` 明文只認這個身分，
        教官控台自己打絕對 403。exercise_id 由 Range Core 生成（見 ADR 0003
        「reserves an exercise identifier」），這裡原樣回傳，不自己編一個。"""
        return self._request("POST", "/api/exercises/prepare", {"scenario_id": scenario_id},
                              read_response=True)

    def current_preparation(self) -> dict | None:
        """反代教官查詢目前是否有一筆 `prepared`（#163）——同 `prepare` 的身分
        理由。沒有時 Range Core 回 404，這裡轉成 `None` 而不是往外拋——「沒有
        prepared」本來就是合法狀態，不是錯誤。"""
        try:
            return self._request("GET", "/api/exercises/prepared", read_response=True)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def cancel_preparation(self, exercise_id: str) -> bool:
        """反代教官取消一筆 `prepared`（#163）。回傳是否真的有東西被取消——
        404（早就不是 prepared 了）回 `False`，不當例外拋出，呼叫端可以照樣
        把「取消」當成冪等操作用。

        跟 `revoke_player` 不一樣：這裡刻意**不**吃 `_request` 對 DELETE 404
        的預設吞掉行為（`idempotent_delete=False`）——那個吞掉是為了讓
        `revoke_player` 靜默冪等，但這裡呼叫端需要知道「到底有沒有真的取消
        到東西」才能決定要不要顯示「已經不是 prepared 了」這種提示。"""
        try:
            self._request(
                "DELETE", f"/api/exercises/prepared/{exercise_id}", idempotent_delete=False
            )
            return True
        except HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def _request(self, method: str, path: str, body: dict | None = None,
                 *, read_response: bool = False, idempotent_delete: bool = True) -> dict | None:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            self.base_url + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status not in (200, 201, 204):
                    raise RuntimeError(f"Range Core returned {response.status}")
                return json.loads(response.read()) if read_response else None
        except HTTPError as exc:
            if method == "DELETE" and idempotent_delete and exc.code == 404:
                return None
            raise
