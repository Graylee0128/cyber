"""封鎖派送 —— Range Core 呼叫 Z-MGMT 的 response queue（#51／WS3 spec §5.2）。

Range Core 是編排者：Blue Action 先落地（`blue_action_store.py`），
`contain` 落地成功後才呼叫這裡派送。**只組出跨區契約要的欄位，純
dict，不 import `purple.response.queue` 的 dataclass** —— `range_core`
刻意不 import `purple`（`tests/range_core/test_boundary.py` 機械檢查）；
兩邊各自 import 自己的型別，共用的只有 wire 上的 JSON 形狀。

為什麼是「回傳 False」不是「拋例外」：AC 明講「派送失敗時該次動作有
明確狀態」——呼叫端（`api.py`）要把這個結果寫回 Blue Action 那一列
（`BlueActionStore.set_dispatch_status`），例外會打斷那個寫入，讓失敗
變成「連狀態都沒留下」，比「留下 failed 狀態」還糟。

**Range Core 是封鎖路徑的單點（AC 明講要記在文件裡）**：`contain` 只有
這一條路徑能把 Blue Action 落地跟派送接起來——`HttpResponseDispatcher`
是唯一持有 `RANGE_CORE_RESPONSE_TOKEN`（Z-MGMT 認得的那把密鑰）的呼叫
者。Range Core 若掛了（或部署沒配到這個 token／URL），藍隊的「按下封鎖
鈕」在 UI 上看起來像成功送出（`POST /api/blue-actions` 本身仍會回 201，
因為落地不受派送影響），但派送一律回 `False`、`dispatch_status` 一律
`"failed"`、`/api/score` 也不會給 `Contain < 60 sec` 的分——不是「靜默
沒反應」，是「看得到失敗，但真的封鎖不動」。這是刻意的架構代價（WS3
spec §5.2 排除了 Console 雙寫與從 `response.*` 事件反推兩條替代路徑，
理由見 issue #51），不是本模組的缺陷。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

import psycopg

#: Range Core 呼叫 Z-MGMT enqueue 端點用的 token（部署時注入）。跟
#: `RANGE_CORE_TOKEN_*`（Range Core 驗證*別人*打進來的呼叫）方向相反——
#: 這是 Range Core 自己*打出去*要出示的憑證，因此不共用同一個變數。
RESPONSE_TOKEN_ENV = "RANGE_CORE_RESPONSE_TOKEN"
#: Z-MGMT 的 base URL（部署時注入，例如 http://10.167.10.21:8000）。
RESPONSE_URL_ENV = "PURPLE_RESPONSE_URL"
DEFAULT_TIMEOUT_S = 5.0


class ResponseDispatcher(Protocol):
    """把一個 `contain` 動作變成真正送進佇列的封鎖命令。

    回傳派送是否成功——派送失敗不是例外，是一個要被記錄下來的結果。
    """

    def dispatch(self, exercise_id: str, event_id: str) -> bool: ...


class UnroutableEvent(ValueError):
    """Core Event 找不到，或缺少封鎖需要的欄位（沒有 source_ip／service）。"""


def _core_event_or_raise(conn: psycopg.Connection, exercise_id: str, event_id: str) -> dict:
    row = conn.execute(
        "SELECT event FROM core_events "
        "WHERE exercise_id = %s AND event_id = %s AND lifecycle = 'firing'",
        (exercise_id, event_id),
    ).fetchone()
    if row is None:
        raise UnroutableEvent(f"no Core Event {event_id!r} on record for exercise {exercise_id!r}")
    return row[0]


def _command_payload(exercise_id: str, event_id: str, event: dict) -> dict:
    target = event.get("target") or {}
    source_ip = target.get("source_ip")
    service = target.get("service")
    if not source_ip or not service:
        raise UnroutableEvent(
            f"event {event_id!r} is missing target.source_ip/target.service; cannot route a block"
        )
    return {
        "event_id": event_id,
        "source_ip": source_ip,
        "exercise_id": exercise_id,
        "scenario_id": event.get("scenario_id", ""),
        "severity": event.get("severity", ""),
        "technique": event.get("technique", ""),
        "service": service,
    }


@dataclass
class HttpResponseDispatcher:
    """production 實作：對 Z-MGMT 發一次 HTTP POST。

    刻意用 stdlib `urllib`，不引入 httpx 之類的用戶端函式庫當 production
    依賴——這通呼叫是同步、低頻（一場演練最多幾十次 `contain`），沒有
    需要非同步／連線池的規模。
    """

    conn: psycopg.Connection
    base_url: str | None = None
    token: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S

    def dispatch(self, exercise_id: str, event_id: str) -> bool:
        base_url = self.base_url or os.environ.get(RESPONSE_URL_ENV)
        token = self.token or os.environ.get(RESPONSE_TOKEN_ENV)
        if not base_url or not token:
            # 部署沒接好這條路是設定錯誤，不是「這次剛好失敗」，但對呼叫端
            # 而言結果一樣：沒有派送成功，回 False 讓上層記下明確狀態，
            # 不要在這裡拋例外打斷「至少把狀態記下來」這件事。
            return False

        try:
            event = _core_event_or_raise(self.conn, exercise_id, event_id)
            payload = _command_payload(exercise_id, event_id, event)
        except UnroutableEvent:
            return False

        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/response/enqueue",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False
