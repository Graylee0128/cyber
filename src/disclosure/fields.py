"""欄位級遮蔽表 —— 哪個欄位要到哪一級 clearance 才看得到真值（#49／WS3 spec §2）。

事件級的 `visibility` 決定「這筆事件誰看得到」；本表決定「同一筆事件，誰看得到
**哪些欄位**」。兩者同軸（`VISIBILITY_RANK`），不是兩套權限模型。

**這是資料，不是邏輯。** 新增一個要遮的欄位＝在 `FIELD_MASKING` 加一列，
`disclosure.projection` 一個字都不用改。散在程式裡的 if 會漂，表不會。

為什麼 `rule` 也在表上：Grafana rule 的標題本身就是答案 —— `SSHBruteForce`
就是 T1110 Brute Force、`SQLInjectionBurst` 就是 T1190。遮了 `technique` 卻照樣
回 rule 名稱，`Identify Technique +100` 仍然是白送的（#49「繞道防護」驗收條件）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MaskStrategy(StrEnum):
    """看不到真值時，那個欄位變成什麼。"""

    #: 整個鍵拿掉。呼叫端看到「沒有這個欄位」，而不是一個看起來像值的東西。
    DROP = "drop"
    #: 換成穩定的匿名標籤（如 `Detection #2`）。同一個真值在整場演練固定對到同一個
    #: 標籤，藍隊仍可判斷「又是同一條規則觸發的」，但標籤本身不帶語意。
    #: 與 Battleboard 的 `Attack #N`（`purple.battleboard.sanitize`）是同一個手法。
    LABEL = "label"


@dataclass(frozen=True)
class FieldPolicy:
    """一個欄位的遮蔽政策。"""

    #: 看得到真值所需的最低 clearance，用 `VISIBILITY_RANK` 的字彙表示。
    min_visibility: str
    strategy: MaskStrategy
    #: LABEL 策略的標籤前綴。DROP 時無意義。
    label_prefix: str | None = None


#: 欄位 → 政策。**表以外沒有第二個地方定義誰看得到什麼欄位。**
#:
#: `technique`：WS3 spec §2.1 —— 藍隊要自己判讀並提交，答案不能寫在告警上；
#: §2.2 紅隊一起遮（否則紅隊看得到、藍隊看不到，荒謬）。
#: `rule`：見模組 docstring —— rule 名稱就是技法名稱的白話版。
FIELD_MASKING: dict[str, FieldPolicy] = {
    "technique": FieldPolicy(min_visibility="purple", strategy=MaskStrategy.DROP),
    "rule": FieldPolicy(
        min_visibility="purple",
        strategy=MaskStrategy.LABEL,
        label_prefix="Detection",
    ),
}
