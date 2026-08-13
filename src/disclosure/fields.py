"""欄位級遮蔽表 —— 哪個欄位對哪個 audience 不揭露。

內容來源：#75 四畫面欄位級揭露矩陣（尚未定案）。
本檔先建立位置，讓兩個出口有明確的單一來源。
"""

from __future__ import annotations

#: 空表 = 目前不遮任何欄位。填入前行為與現況一致。
FIELD_MASKING: dict = {}
