"""共用 fixture。

`clock_in_sync` 是票 01 的交付物之一：後續所有契約測試（02b、03、08、09、10）
都要求它，時鐘沒同步就不必往下跑 —— 那時候測出來的 MTTD 與時序關聯是假的。
"""

from pathlib import Path

import pytest

from purple.clock.config import ConfigError, load_config
from purple.clock.runner import check

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOCK_CONFIG = REPO_ROOT / "config" / "clock-nodes.yaml"


@pytest.fixture(scope="session")
def clock_in_sync():
    """時鐘同步的前置條件。不同步就讓測試 **失敗**，不是 skip。

    skip 是這類前置檢查退化成永遠綠的標準路徑：檢查壞了 → 自動跳過 →
    沒人發現。要跳過必須是明確的人為決定，不是預設行為。
    """
    try:
        config = load_config(CLOCK_CONFIG)
    except ConfigError as exc:
        pytest.fail(f"clock config unusable: {exc}")

    report = check(config)
    if not report.ok:
        pytest.fail(f"時鐘未同步，後續時序斷言不可信：\n{report.summary()}")
    return report
