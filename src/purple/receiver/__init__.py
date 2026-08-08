"""P1 Webhook Receiver —— 票 02b 的 stub，票 03 實作。

02b 刻意讓端到端測試紅著交票：這裡不含任何 receiver 邏輯，
所以 Core Event 不會出現，測試以「沒有收到事件」失敗（而非載具自身錯誤）。
"""

from __future__ import annotations

from typing import Any


def ingest_alert(webhook: dict[str, Any], *, events: Any, records: Any, adapter: Any = None) -> list[str]:
    """把一個 Grafana webhook 轉成 Core Event。

    票 03 實作。02b 階段刻意不做任何事，讓 02b 的紅燈成立。
    """
    return []  # 03 implements
