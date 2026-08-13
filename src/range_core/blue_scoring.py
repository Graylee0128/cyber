"""藍隊計分 —— 純函數，計分參數取自平台設定（#49／WS3 spec §4.3、§4.5）。

與 `range_core.scoring`（紅隊）同一種模式：**分數是推導出來的，不存欄位**
（WS5 spec §1.3）。本模組沒有 I/O，所以結構上碰不到 `purple.metrics` ——
WS5 spec §1.1 明文禁止拿 P2 的 MTTR／containment duration 計分。

時間模型（WS3 spec §4.5）——**兩段共用同一個起點，不是接力**：

    Core Event ──────► acknowledge   ＝ 注意到的速度 → Detect Attack
         │
         └───────────► contain       ＝ 處置速度     → Contain < 60 sec

藍隊跳過 `acknowledge` 直接 `contain`，`Contain < 60 sec` 仍然成立。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from range_core.blue_actions import (
    BlueActionLog,
    BlueActionType,
    ExecutionEvidence,
    TechniqueTruth,
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "blue-scoring.yaml"

OBJECTIVE_KEYS = ("detect_attack", "identify_technique", "contain", "resolve_incident")


class BlueScoringConfigError(RuntimeError):
    """計分設定壞了或缺欄位。fail loud —— 靜默用預設值等於偷偷換一把尺。"""


@dataclass(frozen=True)
class BlueScoringConfig:
    """平台級計分參數。**scenario 讀不到這裡，也覆寫不了**（WS3 spec §4.3）。"""

    points: dict[str, int]
    contain_threshold_seconds: int

    @classmethod
    def load(cls, path: Path | str | None = None) -> BlueScoringConfig:
        target = Path(
            path
            if path is not None
            else os.environ.get("BLUE_SCORING_CONFIG", DEFAULT_CONFIG_PATH)
        )
        try:
            raw = yaml.safe_load(target.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise BlueScoringConfigError(f"blue scoring config unusable ({target}): {exc}") from exc

        if not isinstance(raw, dict):
            raise BlueScoringConfigError(f"{target}: expected a mapping at the top level")

        objectives = raw.get("objectives")
        if not isinstance(objectives, dict):
            raise BlueScoringConfigError(f"{target}: missing 'objectives' mapping")

        points: dict[str, int] = {}
        for key in OBJECTIVE_KEYS:
            value = objectives.get(key)
            if not isinstance(value, int):
                raise BlueScoringConfigError(f"{target}: objectives.{key} must be an integer")
            points[key] = value

        threshold = raw.get("contain_threshold_seconds")
        if not isinstance(threshold, int) or threshold <= 0:
            raise BlueScoringConfigError(
                f"{target}: contain_threshold_seconds must be a positive integer"
            )

        return cls(points=points, contain_threshold_seconds=threshold)


@dataclass(frozen=True)
class EventScore:
    """一個 event 的藍隊結果。時間差一律以 Core Event 的 `observed_at` 為起點。"""

    event_id: str
    awarded: int
    detect_seconds: float | None
    contain_seconds: float | None
    #: 判讀結果：`correct`／`wrong`／`dismissed_correctly`／`dismissed_wrongly`／None（沒判讀）
    judgement: str | None
    resolved: bool

    @property
    def reaction_time_available(self) -> bool:
        """**串位防護**：藍隊沒有任何動作時，反應時間是「不可得」，不是 0。"""
        return self.detect_seconds is not None or self.contain_seconds is not None


@dataclass(frozen=True)
class BlueScoreboard:
    total: int
    events: tuple[EventScore, ...]

    def as_dict(self) -> dict:
        return {
            "blue": {
                "total": self.total,
                "events": [
                    {
                        "event_id": e.event_id,
                        "awarded": e.awarded,
                        "detect_seconds": e.detect_seconds,
                        "contain_seconds": e.contain_seconds,
                        "judgement": e.judgement,
                        "resolved": e.resolved,
                    }
                    for e in self.events
                ],
            }
        }


def _delta_seconds(observed_at: datetime, action_at: datetime | None) -> float | None:
    if action_at is None:
        return None
    return (action_at - observed_at).total_seconds()


def score_event(
    event_id: str,
    observed_at: datetime,
    log: BlueActionLog,
    config: BlueScoringConfig,
    truth: TechniqueTruth,
    evidence: ExecutionEvidence,
) -> EventScore:
    """一個 event 的藍隊得分（純函數）。"""
    acknowledge = log.first(event_id, BlueActionType.ACKNOWLEDGE)
    contain = log.first(event_id, BlueActionType.CONTAIN)
    resolve = log.first(event_id, BlueActionType.RESOLVE)
    judgement_action = log.judgement_for(event_id)

    detect_seconds = _delta_seconds(observed_at, acknowledge.submitted_at if acknowledge else None)
    contain_seconds = _delta_seconds(observed_at, contain.submitted_at if contain else None)

    awarded = 0
    if acknowledge is not None:
        awarded += config.points["detect_attack"]

    # Contain 量的是 Core Event → contain，**不是** acknowledge → contain。
    # 沒 acknowledge 直接 contain 照樣成立。
    if contain_seconds is not None and contain_seconds <= config.contain_threshold_seconds:
        awarded += config.points["contain"]

    if resolve is not None:
        awarded += config.points["resolve_incident"]

    judgement: str | None = None
    if judgement_action is not None:
        if judgement_action.action is BlueActionType.CLASSIFY:
            correct = (
                truth.technique_for(event_id) is not None
                and judgement_action.technique == truth.technique_for(event_id)
            )
            judgement = "correct" if correct else "wrong"
            if correct:
                awarded += config.points["identify_technique"]
        else:
            # 沒有執行證據 → 這條規則誤觸 → 藍隊判誤報正確。裁決來源與偵測結果無關。
            correct = not evidence.has_evidence(event_id)
            judgement = "dismissed_correctly" if correct else "dismissed_wrongly"

    return EventScore(
        event_id=event_id,
        awarded=awarded,
        detect_seconds=detect_seconds,
        contain_seconds=contain_seconds,
        judgement=judgement,
        resolved=resolve is not None,
    )


def derive_blue_scores(
    observed_at_by_event: dict[str, datetime],
    log: BlueActionLog,
    config: BlueScoringConfig,
    truth: TechniqueTruth,
    evidence: ExecutionEvidence,
) -> BlueScoreboard:
    """整場的藍隊分數。歸屬是 **event 級**，粗粒度統計由這裡聚合（WS5 spec §1.2）。"""
    events = tuple(
        score_event(event_id, observed_at, log, config, truth, evidence)
        for event_id, observed_at in observed_at_by_event.items()
    )
    return BlueScoreboard(total=sum(e.awarded for e in events), events=events)
