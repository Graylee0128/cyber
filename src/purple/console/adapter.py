"""Console 的 API adapter（票 #26）—— 把 Evaluation API 的 JSON 接成 view model。

這是「表格由 Evaluation API 資料產生、Console 不自行計算比率」那條 AC 的落地：
Console 拿的是兩個**已算好**的端點輸出，只做 join 與投影，沒有任何比率運算。

    GET /api/exercises/{id}/actions      → registry：action_id → technique
    GET /api/exercises/{id}/evaluation   → 四態結果 + metrics（比率在這，Console 只轉呈）

比率一律取 `evaluation["metrics"]` 原值往下傳；本模組不出現任何除法。
"""

from __future__ import annotations

from typing import Any

from purple.console.coverage import (
    ActionOutcome,
    CoverageRow,
    TechniqueMeta,
    build_coverage_matrix,
)
from purple.evaluation.evaluator import ActionState


def coverage_from_api(
    *,
    registry: dict[str, Any],
    evaluation: dict[str, Any],
    techniques: dict[str, TechniqueMeta],
) -> list[CoverageRow]:
    """把 registry 與 evaluation 兩份 API 輸出 join 成 Coverage 表。

    - `registry["actions"]` 提供 action_id → technique（畫面一按 technique 聚合要用）。
    - `evaluation["actions"]` 提供每個 action 的四態與 gap。
    - 只有同時出現在兩邊的 action 才進表 —— evaluation 有、registry 沒有的 action
      無從得知 technique，直接略過（不猜）。
    """
    technique_of = {a["action_id"]: a["technique"] for a in registry.get("actions", [])}
    outcomes: list[ActionOutcome] = []
    for action in evaluation.get("actions", []):
        technique = technique_of.get(action["action_id"])
        if technique is None:
            continue
        outcomes.append(
            ActionOutcome(
                action_id=action["action_id"],
                technique=technique,
                state=ActionState(action["state"]),
                gap=action.get("gap"),
            )
        )
    return build_coverage_matrix(outcomes, techniques)


def coverage_metrics(evaluation: dict[str, Any]) -> dict[str, Any]:
    """比率原值轉呈 —— Console 顯示的分母／涵蓋率一律取自 API，不重算。"""
    return evaluation.get("metrics", {})
