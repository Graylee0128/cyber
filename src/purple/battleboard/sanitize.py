"""Raw purple evaluation output → Battleboard-safe public event (#82).

`Raw Security Event → Normalization → Sanitization → Public Battle Event` (SA §5.4).
Pure functions, no I/O — same convention as `evaluation/evaluator.py` and `metrics/gaps.py`.

以下兩個決策於 2026-08-13 拍板（見 `.scratch/battleboard-ui/spec.md` Q3/Q4 與 #82 留言）：

  Q3 — technique 一律匿名化為 `Attack #N`；真實 MITRE ID 永遠不進這個模組的公開輸出。
  Q4 — 即時 hit/miss 狀態只給 Instructor；公開層在回合結束前維持中性狀態（`PENDING`），
       避免在藍隊自己的 SOC 判定之前，Battleboard 就把「有沒有被打穿」現場公布出來。

SA §5.4 不得公開清單（Rule threshold／Raw payload／Detection query／Secret／Ban TTL／
Internal IP mapping／Falco rule detail）不是靠命名約定排除——這些欄位在
`PublicBattleEvent` 裡根本不存在，沒有欄位可以外洩。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from purple.evaluation.evaluator import ActionResult, ActionState


class PublicAttackState(StrEnum):
    """§3.9：公開層只能用狀態，不能用裸比率。"""

    UNRESOLVED = "○"
    BREACHED = "🔴"
    DEFENDED = "🟢"
    IN_PROGRESS = "🟡"


class Disclosure(StrEnum):
    """Q4：回合結束前，公開層看到的是 PENDING，不是真實狀態。"""

    PENDING = "pending"
    REVEALED = "revealed"


#: ActionState → 揭露後的公開狀態。HIT＝藍隊偵測到（守住了），MISS＝藍隊沒偵測到（被打穿）。
#: UNKNOWN／NOT_EXECUTED 在公開層看起來一樣：還沒有可陳述的結果。
_REVEALED_STATE_BY_ACTION_STATE: dict[ActionState, PublicAttackState] = {
    ActionState.HIT: PublicAttackState.DEFENDED,
    ActionState.MISS: PublicAttackState.BREACHED,
    ActionState.UNKNOWN: PublicAttackState.UNRESOLVED,
    ActionState.NOT_EXECUTED: PublicAttackState.UNRESOLVED,
}


@dataclass(frozen=True)
class PublicBattleEvent:
    """Battleboard 唯一能吃到的事件形狀。沒有 rule/payload/query/secret/ttl/ip 欄位。"""

    attack_label: str
    team: str
    state: PublicAttackState
    disclosure: Disclosure


@dataclass(frozen=True)
class InstructorBattleEvent:
    """Instructor-only 投影：即時真實狀態，但技法仍是匿名標籤。

    Instructor 是全知角色，看得到真實 technique——只是不從這裡看。Instructor Console
    另有 Raw Event 通道（#75 §1.2），這支模組不必兼任那個通道。
    """

    attack_label: str
    team: str
    state: PublicAttackState


def build_attack_label_map(action_ids: list[str]) -> dict[str, str]:
    """依註冊順序把 action_id 對映到 `Attack #N`。順序來自 Frozen Action Registry 的
    插入順序，不是技法字母序——不能讓標籤順序反過來洩漏技法分類。"""

    return {action_id: f"Attack #{i}" for i, action_id in enumerate(action_ids, start=1)}


def project_public_event(
    result: ActionResult,
    *,
    attack_label: str,
    team: str,
    round_ended: bool,
) -> PublicBattleEvent:
    """Q4：回合未結束時,不論真實 state 是什麼,公開層一律看到 PENDING + UNRESOLVED。"""

    if not round_ended:
        return PublicBattleEvent(
            attack_label=attack_label,
            team=team,
            state=PublicAttackState.UNRESOLVED,
            disclosure=Disclosure.PENDING,
        )

    state = _REVEALED_STATE_BY_ACTION_STATE[result.state]
    return PublicBattleEvent(
        attack_label=attack_label,
        team=team,
        state=state,
        disclosure=Disclosure.REVEALED,
    )


def project_instructor_event(
    result: ActionResult,
    *,
    attack_label: str,
    team: str,
) -> InstructorBattleEvent:
    """Instructor 永遠看即時真實狀態，不受 Q4 的 pending 限制。"""

    return InstructorBattleEvent(
        attack_label=attack_label,
        team=team,
        state=_REVEALED_STATE_BY_ACTION_STATE[result.state],
    )


def format_score_fraction(numerator: int, denominator: int) -> str | None:
    """§3.9：分數形式必須看得見分母（`8/10`），永遠不是裸百分比（`82%`）。

    分母為 0 時回 None，不假裝一個 `0/0`——那和 evaluator.py 對 coverage 分母為 0
    時回 None（而非假裝 0%）是同一個原則。
    """

    if denominator <= 0:
        return None
    if numerator < 0 or numerator > denominator:
        raise ValueError(f"numerator {numerator} out of range for denominator {denominator}")
    return f"{numerator}/{denominator}"
