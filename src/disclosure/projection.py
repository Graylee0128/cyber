"""欄位級投影 —— 把 `FIELD_MASKING` 套到一份要回給呼叫者的 payload 上。

**遮蔽發生在 API 回應的組裝，不是前端渲染**（#49）。前端遮是假的：devtools 打開
就看到真值。兩個對外出口（`purple.evidence` 的 Evidence API、`range_core` 的
SSE）都呼叫這裡，規則只有一份。

**落地內容不變**：本模組只作用在「要回給誰」的那份 dict 上，Core Event 存進
`core_events` 的內容一個字都不動 —— 否則 P2 的 coverage 就算不出來了。

純函數，無 I/O。與 `evidence/resolver.py` 的 `filter_by_visibility`（事件級／行級）
同一個位置、同一個慣例，只是粒度到欄位。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from disclosure.clearance import visibility_rank
from disclosure.fields import FIELD_MASKING, FieldPolicy, MaskStrategy


def build_label_map(values: Sequence[str], prefix: str) -> dict[str, str]:
    """真值 → 穩定匿名標籤（`Detection #1`、`Detection #2`…）。

    順序取自**註冊順序**（Grafana rules 檔的出現順序），不是字母序 ——
    字母序會讓標籤順序本身洩漏分類，比照 `build_attack_label_map` 的理由。

    重複值只佔一個號碼；空字串忽略。
    """
    labels: dict[str, str] = {}
    for value in values:
        if value and value not in labels:
            labels[value] = f"{prefix} #{len(labels) + 1}"
    return labels


def _masked_value(
    value: Any, policy: FieldPolicy, labels: Mapping[str, Mapping[str, str]], field: str
) -> tuple[bool, Any]:
    """(要不要保留這個鍵, 保留的話值是什麼)。

    LABEL 策略查不到對應標籤時 **一律 DROP** —— 查不到就寧可少一個欄位，
    絕不退回真值。fail closed 是這張表唯一可接受的失敗方向。
    """
    if policy.strategy is MaskStrategy.DROP:
        return False, None

    label = labels.get(field, {}).get(value) if isinstance(value, str) else None
    if label is None:
        return False, None
    return True, label


def project_fields(
    payload: Mapping[str, Any],
    clearance: int,
    *,
    labels: Mapping[str, Mapping[str, str]] | None = None,
    masking: Mapping[str, FieldPolicy] = FIELD_MASKING,
) -> dict[str, Any]:
    """回傳套用欄位遮蔽後的新 dict（不改動輸入）。

    `clearance` 是呼叫者的等級（`CALLER_CLEARANCE` 查出來的數字），**不是身分
    字串** —— 身分怎麼換出來是 `disclosure.identity` 的事，這裡只吃已解析的等級，
    免得同一件事有兩個地方判。

    `labels` 是 LABEL 策略要用的對照表，形如 `{"rule": {真值: 標籤}}`，
    由各出口在組裝回應時注入（來源是自己那份註冊清單）。
    """
    label_maps = labels or {}
    projected: dict[str, Any] = {}

    for field, value in payload.items():
        policy = masking.get(field)
        if policy is None or clearance >= visibility_rank(policy.min_visibility):
            projected[field] = value
            continue

        keep, masked = _masked_value(value, policy, label_maps, field)
        if keep:
            projected[field] = masked

    return projected
