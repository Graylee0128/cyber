"""P1 Webhook Receiver —— shell 層：HTTP／儲存／外送。

純函數在 `core.py`。這裡只負責編排順序與 I/O：
鑄造 event_id → 先寫 Alert Record → 再發 Core Event → 外送 → 封鎖。
順序不可顛倒（spec §2.5）：join key 只有一個產生來源。
"""

from __future__ import annotations

from typing import Any

from purple.receiver.adapters import CoreEventAdapter, NoopAdapter
from purple.receiver.core import (
    build_alert_record,
    build_core_event,
    lifecycle_of,
    mint_event_id,
)
from purple.response.direct_block import Blocker, DirectIpsetBlocker

__all__ = ["ingest_alert"]


def ingest_alert(
    webhook: dict[str, Any],
    *,
    events: Any,
    records: Any,
    adapter: CoreEventAdapter | None = None,
    blocker: Blocker | None = None,
) -> list[str]:
    """把一個 Grafana webhook 轉成 Core Event，回傳鑄造出的 event_id 清單。

    每筆 alert：
      1. 決定 lifecycle；pending 之類不產生事件（spec §2.2）
      2. 鑄造 event_id
      3. **先**寫 Alert Record
      4. **再**發 Core Event 到 P1 儲存
      5. 外送給下游（可插拔 adapter）
      6. attack.detected 觸發 ipset 直寫封鎖（票 03 的 expand，票 09 contract）
    """
    adapter = adapter or NoopAdapter()
    blocker = blocker or DirectIpsetBlocker()

    emitted: list[str] = []
    for alert in webhook.get("alerts", []):
        lifecycle = lifecycle_of(alert)
        if lifecycle is None:
            continue  # pending 等內部狀態不是遊戲語意

        event_id = mint_event_id()

        # 順序不可顛倒：Alert Record 先落地，Core Event 才有可對接的遙測細節。
        records.write(build_alert_record(alert, event_id))

        core = build_core_event(alert, event_id, lifecycle)
        events.append(core)
        adapter.deliver(core)

        if core["event_type"] == "attack.detected":
            blocker.block(core)

        emitted.append(event_id)

    return emitted
