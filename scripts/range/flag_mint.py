#!/usr/bin/env python3
"""Flag 產生器（票 #45）—— 每次 `range-up` 鑄一個新 flag。

為什麼獨立成一支可被 import 的模組，而不是在 range-up.sh 裡 `openssl rand`：
flag 的兩個硬性質（每次不同、格式固定）需要**測試接住**，而 shell 裡的隨機字串
沒有測試載體。range-up.sh 呼叫本檔的 CLI（`python3 flag_mint.py`）取得 flag，
`tests/range/test_flag_mint.py` 直接 import `mint_flag()` 驗這兩件事。

flag **絕不烤進 golden**：本檔不在 `lib-cloudimg.sh` 的 `golden_stamp` 來源清單裡，
range-up 產生的 flag 值也只落在 cloud-init 注入檔與 Range Core 的共享檔，兩者都不被
`golden_stamp` 計入。改 flag 不觸發重烤 —— 這正是 §5.1「不烤進 golden」的實作。
格式與防暴力猜測留給實作層（WS2 spec §8），這裡只保證「夠長、每次不同、可被驗」。
"""

from __future__ import annotations

import re
import secrets
import sys

# flag{<32 hex>} —— 32 個 hex（128 bit）猜不到；花括號讓 capture_flag 的提交比對
# 有明確邊界，玩家貼上時不會把前後空白也貼進來。
FLAG_TOKEN_BYTES = 16  # 16 bytes -> 32 hex chars
FLAG_PATTERN = re.compile(r"^flag\{[0-9a-f]{32}\}$")


def mint_flag() -> str:
    """回傳一個新的、密碼學隨機的 flag。連續兩次呼叫幾乎不可能相同。"""
    return f"flag{{{secrets.token_hex(FLAG_TOKEN_BYTES)}}}"


def is_valid_flag(value: str) -> bool:
    """Range Core 比對提交前用它擋掉明顯不合格式的字串。"""
    return bool(FLAG_PATTERN.match(value))


def main() -> int:
    # range-up.sh：`FLAG="$(python3 flag_mint.py)"`。只印 flag、不印任何雜訊，
    # 讓 shell 直接 capture。
    sys.stdout.write(mint_flag() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
