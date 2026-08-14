"""Blue Action 的封閉列舉與判讀紀錄（#49／WS3 spec §4）。

藍隊對 **event 級**操作 —— 不建立 incident 實體（WS3 §3.1）。每筆動作記的是
「動作、時間、對應的 event_id」；**「誰」恆為 `blue`**（隊，不是人），所以這裡
沒有 per-user 欄位（WS3 §5.1 藍隊不個人化）。

本模組是純領域：沒有 HTTP、沒有 SQL。進場端點與持久化屬 #36 的 Blue Action
ingest，本票只交付「五個動詞是什麼、什麼算合法、判讀對不對」。`BlueActionLog`
是那個規則的可測形狀，#36 接上 store 時把同一組判定搬到寫入路徑即可。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class BlueActionType(StrEnum):
    """五個動作的**封閉列舉**（WS3 spec §4.1）。

    開放集合＝分類法會漂＝不同場次的藍隊指標不可比（plan §3.2）。刻意不放
    `escalate`（演練只有一個藍隊，沒有可升級的對象）與 `unblock`（Z-RED 只住
    紅隊，封錯的代價趨近於零）—— 屆時是往列舉加一個值，不是改架構。
    """

    ACKNOWLEDGE = "acknowledge"
    CLASSIFY = "classify"
    CONTAIN = "contain"
    RESOLVE = "resolve"
    DISMISS = "dismiss"


#: 判讀類動作 —— 每個 event 只有一次機會（WS3 spec §4.2）。
#: 兩者共用同一個名額：`dismiss`（我說這是誤報）與 `classify`（我說這是 T1190）
#: 對同一筆事件是互斥的主張。分開計名額等於「先猜誤報，被打槍再猜技法」，
#: 那就是無限重試的變形，把 §2.1「把答案遮掉」整個掏空。
JUDGEMENT_ACTIONS = frozenset({BlueActionType.CLASSIFY, BlueActionType.DISMISS})


class BlueActionRejected(ValueError):
    """動作不合法。**一律帶明確原因** —— 靜默丟棄會讓藍隊以為自己送出去了。"""


@dataclass(frozen=True)
class BlueAction:
    """一次藍隊動作。`team` 沒有欄位：恆為 blue，不存一個永遠相同的值。"""

    action: BlueActionType
    event_id: str
    submitted_at: datetime
    #: 只有 `classify` 帶：藍隊提交的技法判讀。
    technique: str | None = None


def parse_action(value: str) -> BlueActionType:
    """字串 → 列舉。未列舉的值**被拒絕並回明確原因**，不猜、不預設。"""
    try:
        return BlueActionType(value)
    except ValueError:
        known = ", ".join(sorted(a.value for a in BlueActionType))
        raise BlueActionRejected(
            f"unknown blue action {value!r}; the action list is a closed enumeration: {known}"
        ) from None


def build_action(
    action: str, event_id: str, submitted_at: datetime, technique: str | None = None
) -> BlueAction:
    """組出一筆合法的動作，或說清楚為什麼不合法。"""
    parsed = parse_action(action)

    if not event_id:
        raise BlueActionRejected("blue action must reference an event_id (actions are event-level)")

    if parsed is BlueActionType.CLASSIFY and not technique:
        raise BlueActionRejected("classify must carry the submitted technique")
    if parsed is not BlueActionType.CLASSIFY and technique:
        raise BlueActionRejected(f"{parsed.value} does not take a technique")

    return BlueAction(action=parsed, event_id=event_id, submitted_at=submitted_at, technique=technique)


class BlueActionLog:
    """一場演練的藍隊動作紀錄，並強制「判讀一次定生死」。

    非判讀類動作（`acknowledge`／`contain`／`resolve`）可重複送 —— 重按封鎖鈕
    不該報錯，計分只看第一次（見 `first`）。判讀類的第二次一律拒絕。
    """

    def __init__(self, actions: Iterable[BlueAction] = ()) -> None:
        self._actions: list[BlueAction] = []
        for action in actions:
            self.record(action)

    def record(self, action: BlueAction) -> BlueAction:
        if action.action in JUDGEMENT_ACTIONS and self.judgement_for(action.event_id) is not None:
            spent = self.judgement_for(action.event_id)
            raise BlueActionRejected(
                f"event {action.event_id} was already judged by {spent.action.value!r}; "
                f"classify/dismiss is one shot per event (WS3 spec §4.2)"
            )
        self._actions.append(action)
        return action

    @property
    def actions(self) -> tuple[BlueAction, ...]:
        return tuple(self._actions)

    def judgement_for(self, event_id: str) -> BlueAction | None:
        """該事件已用掉的判讀（`classify` 或 `dismiss`），沒有就 None。"""
        return next(
            (
                a
                for a in self._actions
                if a.event_id == event_id and a.action in JUDGEMENT_ACTIONS
            ),
            None,
        )

    def first(self, event_id: str, action: BlueActionType) -> BlueAction | None:
        """該事件的第一筆指定動作。計分一律看第一次 —— 反應時間量的是最早那次。"""
        return next(
            (a for a in self._actions if a.event_id == event_id and a.action is action),
            None,
        )

    def event_ids(self) -> tuple[str, ...]:
        seen: list[str] = []
        for action in self._actions:
            if action.event_id not in seen:
                seen.append(action.event_id)
        return tuple(seen)


class TechniqueTruth(Protocol):
    """`classify` 的正確答案來源：觸發該事件的規則掛的 technique label。

    平台**已經知道**正確答案，所以判讀可自動評分、不需要教官在場
    （WS3 spec §2.1，與紅隊 `capture_flag` 走提交比對同一個機制）。
    """

    def technique_for(self, event_id: str) -> str | None: ...


class ExecutionEvidence(Protocol):
    """`dismiss` 的裁決來源：靶機 app 對每個請求寫的執行證據。

    **刻意不是偵測規則的結果** —— 拿偵測結果去裁決「這條偵測是不是誤報」是循環
    論證。執行證據的存在與 Grafana 有沒有規則無關（WS2 spec §4.3），所以它能當
    獨立的真相來源。沒有執行證據 → 藍隊判誤報正確。
    """

    def has_evidence(self, event_id: str) -> bool: ...


@dataclass(frozen=True)
class MappingTechniqueTruth:
    """以對照表實作的答案來源（`event_id → technique`）。"""

    by_event: Mapping[str, str]

    def technique_for(self, event_id: str) -> str | None:
        return self.by_event.get(event_id)


@dataclass(frozen=True)
class MappingExecutionEvidence:
    """以對照表實作的執行證據（`event_id → 有沒有執行證據`）。"""

    by_event: Mapping[str, bool]

    def has_evidence(self, event_id: str) -> bool:
        return bool(self.by_event.get(event_id))


class DispatchOutcome(Protocol):
    """`contain` 是否真的把封鎖命令送進 Z-MGMT 的佇列（#51／WS3 spec §5.2）。

    Blue Action 先落地、再派送——落地一定成功（不然整筆動作都不存在），
    派送才會失敗。這個 Protocol 只回答「派送成功了嗎」，不是「動作合不
    合法」，兩者是完全不同的問題：合法但派送失敗的 `contain` 不該給
    `Contain < 60 sec` 的分數——AC 明講「不得出現有分數沒封鎖」。
    """

    def dispatched(self, event_id: str) -> bool: ...


@dataclass(frozen=True)
class MappingDispatchOutcome:
    """以對照表實作的派送結果（`event_id → 派送成功了嗎`）。"""

    by_event: Mapping[str, bool]

    def dispatched(self, event_id: str) -> bool:
        return bool(self.by_event.get(event_id))
