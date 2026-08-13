"""SSE 事件流的資料面（#36 WS5-7）—— 游標、投影、frame 格式。

HTTP 那一層薄到只剩 `StreamingResponse`，判定與格式化都在這裡，才測得動。

## 游標為什麼是 `core_events.seq` 而不是 `recorded_at`

`Last-Event-ID` 續傳要的是**單調遞增且唯一**的游標。`recorded_at` 兩者都不是：
`now()` 是交易開始時間，兩筆並行寫入可能以「時間較早的那筆後 commit」的順序
變成可見 —— 訂閱者的游標已經越過去了，那筆事件就再也補不回來。這正是本票
「補得到斷線期間錯過的事件」要防的情況，所以 `#36` 給 `core_events` 加了
`seq bigserial`（見 `purple/store/db.py`，並同步更新 P1 輸出契約）。

**gap 是允許的**：`ON CONFLICT DO NOTHING` 吃掉的重送仍會消耗一個序號。游標只
需要嚴格遞增，不需要連續。

P1 receiver 會並行處理 webhook，所以單靠 sequence 還不夠：sequence 保證配置順序，
不保證 commit 順序。`purple.store.db` 的 `next_core_event_stream_seq()` 在配置號碼前
取得 transaction advisory lock，讓「配置 seq → commit」成為序列化區段；因此讀者
看見較大的 seq 時，所有較小 seq 都已可見。這個保證落在資料庫 default，不依賴
每個寫入者記得手動加鎖。

`range_core` 讀 `core_events` 是**唯讀的已發布表契約**，與 `telemetry.py` 的
`CoreEventFeed` 同一個手法：不 import `purple`（`tests/range_core/test_boundary.py`
機械檢查）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from disclosure import project_fields, visibility_rank

#: 一次查詢最多取幾筆。續傳時斷線越久補得越多，但不能一口氣把整場撈成一個回應。
DEFAULT_BATCH = 200


@dataclass(frozen=True)
class StreamEvent:
    """一筆待推送的事件。`seq` 就是 SSE 的 `id:`。"""

    seq: int
    event: dict[str, Any]


@dataclass(frozen=True)
class CoreEventStream:
    """`core_events` 的游標式讀取（唯讀，已發布表契約）。"""

    conn: psycopg.Connection

    def after(self, exercise_id: str, after_seq: int, limit: int = DEFAULT_BATCH) -> tuple[StreamEvent, ...]:
        rows = self.conn.execute(
            """
            SELECT seq, event FROM core_events
            WHERE exercise_id = %s AND seq > %s
            ORDER BY seq
            LIMIT %s
            """,
            (exercise_id, after_seq, limit),
        ).fetchall()
        return tuple(StreamEvent(seq=row[0], event=row[1]) for row in rows)

    def latest_seq(self, exercise_id: str) -> int:
        """目前最大的 seq。新訂閱者（沒帶 `Last-Event-ID`）從這裡之後開始收 ——
        一連上線就把整場歷史灌給他不是「即時推達」，是回放。"""
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM core_events WHERE exercise_id = %s",
            (exercise_id,),
        ).fetchone()
        return int(row[0]) if row else 0


def parse_last_event_id(raw: str | None) -> int:
    """`Last-Event-ID` header → 游標。壞值當成「沒帶」（0），不炸掉重連。

    瀏覽器會原封不動把上次收到的 `id:` 送回來，但那個值最終是使用者可控的字串，
    所以只接受非負整數，其餘一律從頭開始。
    """
    if raw is None:
        return 0
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return 0
    return value if value >= 0 else 0


def visible_to(event: Mapping[str, Any], clearance: int) -> bool:
    """事件級過濾：clearance 不足就整筆看不到（既有 `visibility` 模型）。"""
    return visibility_rank(str(event.get("visibility", "instructor"))) <= clearance


def project_event(
    event: Mapping[str, Any],
    clearance: int,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """欄位級遮蔽 —— 直接用 #49 的共用契約，本模組不另做一份規則。

    紅藍收到的 `attack.detected` 不含 `technique`；purple／instructor 含。
    落地的 Core Event 一個字不動，遮的只是推出去的這份。
    """
    return project_fields(event, clearance, labels=labels)


def sse_frame(seq: int, payload: Mapping[str, Any]) -> str:
    """一個 SSE frame。`id:` 帶 seq，客戶端斷線後會用它當 `Last-Event-ID`。"""
    body = json.dumps(payload, ensure_ascii=False)
    return f"id: {seq}\ndata: {body}\n\n"


def comment_frame(text: str) -> str:
    """SSE 註解行（`:` 開頭）。用來當 keep-alive —— 不是事件，客戶端不會計數，
    也不會動到 `Last-Event-ID`。"""
    return f": {text}\n\n"


def frames_for(
    events: tuple[StreamEvent, ...],
    clearance: int,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> Iterator[tuple[int, str]]:
    """看得到的事件 → `(seq, frame)`。看不到的不產生 frame。

    **呼叫端的游標要推進到整批的最大 seq，不是最後一個 yield 的 seq** ——
    否則一批事件全被過濾掉時，訂閱者會永遠卡在同一個位置反覆重讀。
    """
    for item in events:
        if not visible_to(item.event, clearance):
            continue
        yield item.seq, sse_frame(item.seq, project_event(item.event, clearance, labels))
