"""P1 Webhook Receiver —— shell 層：HTTP／儲存／外送。

純函數在 `core.py`。這裡只負責編排順序與 I/O：
鑄造 event_id → 先寫 Alert Record → 再發 Core Event → 外送 → 封鎖。
順序不可顛倒（spec §2.5）：join key 只有一個產生來源。
"""

from __future__ import annotations

import logging
from typing import Any

from purple.receiver.adapters import CoreEventAdapter, NoopAdapter
from purple.receiver.core import (
    build_alert_record,
    build_core_event,
    lifecycle_of,
    mint_event_id,
)
from purple.receiver.whitelist import TechniqueRejected
from purple.response.queue import CommandQueue, ResponseCommand

__all__ = ["ingest_alert"]

log = logging.getLogger("purple.receiver")


def ingest_alert(
    webhook: dict[str, Any],
    *,
    events: Any,
    records: Any,
    adapter: CoreEventAdapter | None = None,
    response_queue: CommandQueue | None = None,
    fingerprints: Any = None,
    exercise_id: str,
    auto_response: bool = False,
) -> list[str]:
    """把一個 Grafana webhook 轉成 Core Event，回傳鑄造出的 event_id 清單。

    每筆 alert：
      1. 決定 lifecycle；pending 之類不產生事件（spec §2.2）
      2. 取得 event_id：有 fingerprint index 時，firing 與 resolved 依 fingerprint
         共用同一個 event_id（票 05）；否則每筆獨立鑄造
      3. **先**寫 Alert Record
      4. **再**發 Core Event 到 P1 儲存
      5. 外送給下游（可插拔 adapter）
      6. attack.detected 把封鎖需求**放進佇列**（票 09 的 contract）——但只在
         `auto_response=True`（測試載具／demo，票 48）時才自動 enqueue，且標記
         `triggered_by="auto"`，計分（#33）不採計。
         演練預設 `auto_response=False`：這裡只產生「待處置建議」（就是這筆
         attack.detected 事件本身，Blue SOC Console 讀得到），真正 enqueue
         由藍隊按下動作觸發（#51），佇列/agent pull/ipset 這條鏈完全不變。
         不管哪個模式都不直寫 ipset —— 直寫需要連入 target，會破壞
         TARGET→MGMT 單向。實際封鎖永遠由 target 側 agent 主動拉取佇列後執行
         （response/agent.py）。
    """
    adapter = adapter or NoopAdapter()

    emitted: list[str] = []
    for alert in webhook.get("alerts", []):
        lifecycle = lifecycle_of(alert)
        if lifecycle is None:
            continue  # pending 等內部狀態不是遊戲語意

        event_id, attributed_to = _event_id_for(alert, lifecycle, fingerprints, exercise_id)

        # 先在記憶體裡建 Core Event（會驗白名單）。白名單外的 technique 在此被擋，
        # 記錄後跳過 —— 不得靜默通過，也不會留下孤兒 Alert Record。
        try:
            core = build_core_event(
                alert,
                event_id,
                lifecycle,
                exercise_id=attributed_to,
            )
        except TechniqueRejected as exc:
            log.warning("拒收 alert：%s（rule=%s）", exc, alert.get("labels", {}).get("alertname"))
            continue

        # 落地順序不可顛倒：Alert Record 先寫，Core Event 才有可對接的遙測細節（spec §2.5）。
        records.write(build_alert_record(alert, event_id))
        is_new = events.append(core)
        adapter.deliver(core)

        # policies.yaml 的 repeat_interval 讓 Grafana 對同一個持續 firing 的 alert
        # 反覆重送 webhook（lifecycle 測試要靠這個才收得到 firing 中的重送）。只在
        # 這次是真的新事件時才 enqueue：重送不該讓同一次攻擊被重複封鎖、重複產生
        # response.executed（#17 real-range 觀測到 repeat_interval=15s 下同一個
        # attack_event_id 十幾秒內連續 enqueue 好幾次）。
        if (
            auto_response
            and is_new
            and core["event_type"] == "attack.detected"
            and response_queue is not None
        ):
            try:
                response_queue.enqueue(ResponseCommand.from_core_event(core, triggered_by="auto"))
            except ValueError as exc:
                # 沒有可信來源 IP 就不封；拿 service 名或猜測值下 ipset 會製造假成功。
                log.warning("不建立 response command：%s（event_id=%s）", exc, event_id)

        emitted.append(event_id)

    return emitted


def _event_id_for(
    alert: dict[str, Any],
    lifecycle: str,
    fingerprints: Any,
    exercise_id: str,
) -> tuple[str, str]:
    """回傳 `(event_id, 這筆事件該歸給哪一場)`。

    有 fingerprint index 且 alert 帶 fingerprint 時，firing／resolved 共用同一個
    event_id（契約 §2.2）。`resolved` 額外跨場次找 firing：換場之後才到的 resolved
    屬於 firing 那一場，歸給當下這場會讓舊場次的 firing 永遠沒有終點。
    """
    fp = alert.get("fingerprint")
    if fingerprints is None or not fp:
        return mint_event_id(), exercise_id

    if lifecycle == "resolved":
        match = fingerprints.pair_with_firing(fp)
        if match is not None:
            return match.event_id, match.exercise_id
        # 配不到 firing 的 resolved（例如 receiver 在 firing 當下沒收到）：
        # 照舊在當前場次鑄造並記住，後續重送才配得回來。

    return fingerprints.assign(fp), exercise_id
