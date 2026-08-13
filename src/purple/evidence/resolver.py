"""Evidence resolver —— 給一個 `event_id`，回傳那個事件前後的上下文窗。

解析流程（spec §4.2）：

    event_id
       ↓
    查 P1 Alert Record  →  取得 grafana rule / 原始 query / labels / backend
       ↓
    按 source + observed_at ± window 查後端（Loki 或 Fake）
       ↓
    依呼叫者身分套用 visibility 過濾
       ↓
    回傳上下文窗（非單一 log 行）

設計分工（沿用 receiver 的 pure core／I-O shell 慣例）：

- **純函數**：`build_query`（組時間窗查詢）、`filter_by_visibility`（逐行過濾）、
  `clearance`。無 I/O，秒級可測。
- **shell**：`EvidenceResolver.resolve`，只負責讀兩份紀錄、呼叫 backend、
  串起上面的純函數。

`resolve` 的唯一輸入是 `event_id` ＋ 呼叫者身分，**不接受呼叫端傳入查詢語法** ——
否則呼叫者就能繞過 visibility 過濾，直接把手伸到權限邊界之外（ticket / ADR ②）。

範圍界線：HTTP endpoint `GET /evidence/{event_id}` 由 P2 的 Evaluation Engine
提供，不在本模組。這裡只交付 resolver 與 Alert Record 的整合。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from purple.evidence.backends import ContextLine, EvidenceBackend, EvidenceQuery

#: 上下文窗的預設半寬。observed_at 前後各取這麼長 —— 足以看清事件前後脈絡，
#: 又不至於把整場演練都撈回來。
DEFAULT_WINDOW = timedelta(minutes=5)

#: visibility 的機密階序。數字越大越機密。
#: public ⊂ blue ⊂ purple ⊂ instructor —— 是一條線性階序，不是互斥集合。
VISIBILITY_RANK = {"public": 0, "blue": 1, "purple": 2, "instructor": 3}

#: 呼叫者身分 → clearance。與 VISIBILITY_RANK 同軸：clearance N 的人看得到
#: rank ≤ N 的行。red 只看得到 public（Battleboard 等級）。
CALLER_CLEARANCE = {"red": 0, "blue": 1, "purple": 2, "instructor": 3}


class EvidenceError(Exception):
    """Evidence 解析的基底例外。"""


class EvidenceNotFound(EvidenceError):
    """`event_id` 沒有對應的 Alert Record 或 Core Event。"""


class UnknownCaller(EvidenceError):
    """呼叫者身分不在 clearance 表內。fail loud：絕不預設成「看得到全部」。"""


@dataclass(frozen=True)
class Caller:
    """呼叫 Evidence API 的身分。clearance 由 `identity` 決定，呼叫者無法自報。"""

    identity: str


@dataclass(frozen=True)
class EvidenceBundle:
    """resolver 的回傳：一段上下文窗，加上定位它的最小 metadata。

    刻意不帶 `backend`／原始 query —— 那些是遙測細節，只住 Alert Record。
    `rule` 是給分析師看「哪條 rule 觸發的」，rule 名不洩漏後端種類。
    """

    event_id: str
    rule: str
    window_start: datetime
    window_end: datetime
    lines: tuple[ContextLine, ...] = field(default_factory=tuple)

    @property
    def line_count(self) -> int:
        return len(self.lines)


def clearance(caller: Caller) -> int:
    """呼叫者的 clearance 等級。未知身分 → 明確拋錯，不靜默放行。"""
    try:
        return CALLER_CLEARANCE[caller.identity]
    except KeyError:
        raise UnknownCaller(
            f"unknown caller identity {caller.identity!r}; "
            f"known: {sorted(CALLER_CLEARANCE)}"
        ) from None


def _visibility_rank(visibility: str) -> int:
    """visibility 的機密階。未知 visibility → fail closed，當成最嚴格一級。"""
    return VISIBILITY_RANK.get(visibility, max(VISIBILITY_RANK.values()))


def filter_by_visibility(lines: Iterable[ContextLine], caller: Caller) -> tuple[ContextLine, ...]:
    """依呼叫者 clearance 逐行過濾上下文（純函數）。

    不同 caller → 不同子集 → 不同內容，這就是
    `visibility_filter_applied_per_caller` 要斷言的行為。過濾在 Engine 這個
    單一收斂點做（ADR ②），前端不各自決定顯示什麼。
    """
    level = clearance(caller)
    return tuple(line for line in lines if _visibility_rank(line.visibility) <= level)


def _parse_observed_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"observed_at must be an ISO-8601 string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError(f"observed_at {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EvidenceError(f"observed_at {value!r} has no timezone")
    return parsed


def telemetry_source(labels: Mapping[str, str]) -> str:
    """要對哪個遙測來源查。

    注意：Core Event 的 `source` 是 "grafana"（告警來源），不是遙測來源。
    遙測來源住在 Alert Record 的 labels 裡：優先 `source`，退而求其次 `service`。

    公開的原因：#90 的證據組裝要用同一條規則判斷「這筆偵測來自哪個 collector」。
    C3 需要兩個**獨立**來源，若兩處各自解讀 labels，同一個 collector 可能被
    數成兩個來源而假性升級。
    """
    return labels.get("source") or labels.get("service") or "unknown"


def build_query(
    record: Mapping[str, Any],
    reference: Mapping[str, Any],
    window: timedelta = DEFAULT_WINDOW,
) -> EvidenceQuery:
    """由 Alert Record（遙測細節）＋ Core Event（observed_at）組出時間窗查詢（純函數）。

    輸入只有兩份已落地的紀錄與固定窗寬 —— **沒有**呼叫端傳入的 query 字串。
    這正是權限邊界：呼叫者不能自帶查詢語法，也就無法越過 visibility 過濾。
    """
    observed_at = _parse_observed_at(reference["observed_at"])
    labels = record.get("labels") or {}
    return EvidenceQuery(
        source=telemetry_source(labels),
        start=observed_at - window,
        end=observed_at + window,
        labels=dict(labels),
    )


@dataclass(frozen=True)
class EvidenceResolver:
    """Evidence 解析的 shell：讀紀錄 → 呼叫 backend → 套純函數。

    依賴以介面/duck-typing 注入，方便單元測試（與 receiver shell 同慣例）：

    - `records`：Alert Record 儲存，需有 `by_id(event_id) -> dict | None`
      （遙測細節：grafana_rule / query / labels / backend）。
    - `events`：Core Event 儲存，需有 `by_id(event_id) -> list[dict]`
      （提供 observed_at；同 id 可能有 firing／resolved 兩筆）。
    - `backend`：滿足 `EvidenceBackend` 的任一實作。**換掉 Loki 只換這個。**
    """

    records: Any
    events: Any
    backend: EvidenceBackend
    window: timedelta = DEFAULT_WINDOW

    def resolve(self, event_id: str, *, caller: Caller) -> EvidenceBundle:
        """給 `event_id` 回傳依 `caller` 過濾後的上下文窗。

        `event_id` 是唯一的資料輸入；`caller` 只決定看得到多少，不能改查什麼。
        """
        record = self.records.by_id(event_id)
        if record is None:
            raise EvidenceNotFound(f"no alert record for event_id {event_id!r}")

        events = self.events.by_id(event_id)
        if not events:
            raise EvidenceNotFound(f"no core event for event_id {event_id!r}")
        # firing 先於 resolved 落地；以偵測時刻（firing）為窗中心。
        reference = events[0]

        query = build_query(record, reference, self.window)  # 純
        raw = self.backend.fetch_context(query)              # I/O 邊界
        visible = filter_by_visibility(raw, caller)          # 純

        return EvidenceBundle(
            event_id=event_id,
            rule=record["grafana_rule"],
            window_start=query.start,
            window_end=query.end,
            lines=tuple(visible),
        )
