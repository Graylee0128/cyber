"""Purple Console 畫面一 —— ATT&CK Coverage 表投影（票 #26）。

**Console 不自算比率**（AC）。這支只做投影：把 Evaluation API 已判好的四態 action 結果，
按 technique 聚合成一列一列的顯示狀態。比率（coverage、confirmation）由 API 的 metrics
直接呈現，這裡一個除法都不做。

四態直接沿用引擎的 `ActionState`（hit / miss / unknown / not_executed），不新增第五態。
兩個「不進分母」的狀態語意完全不同，必須用不同符號（#26 追加驗收）：

    ✅ hit          藍隊偵測到了
    ❌ miss         紅隊做了、藍隊沒偵測到
    ⏳ unknown      來源掉線，判不出來（設備問題）      —— 不進分母
    未執行 not_executed  紅隊整場沒做，與藍隊無關（劇本沒走完）—— 不進分母

把 not_executed 顯示成 ⏳ 會讓教練誤以為遙測出問題；顯示成 ❌ 更糟 —— 那是直接冤枉藍隊。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from purple.evaluation.evaluator import ActionState


class RedMark(StrEnum):
    """Red 欄：紅隊做了沒。"""

    EXECUTED = "✅"
    NOT_EXECUTED = "未執行"


class BlueMark(StrEnum):
    """Blue 欄：四種互不混用的狀態。"""

    DETECTED = "✅"       # hit
    MISSED = "❌"         # miss
    UNKNOWN = "⏳"        # 來源掉線
    NOT_APPLICABLE = "—"  # 紅隊沒做，藍隊無從偵測


@dataclass(frozen=True)
class ActionOutcome:
    """Console 消費的單筆 action —— Evaluation API 的 action 結果 join 上 registry 的 technique。

    `state` 是引擎判好的四態，`gap` 是引擎算好的 MissClass 值（drill-down 用，這裡不重算）。
    """

    action_id: str
    technique: str
    state: ActionState
    gap: str | None = None


@dataclass(frozen=True)
class TechniqueMeta:
    """techniques.yaml 的一列。`note` 承載判讀限制，Console 一併呈現（§3.6）。"""

    id: str
    name: str
    tactic: str
    note: str | None = None


@dataclass(frozen=True)
class CoverageRow:
    """Coverage 表的一列 —— 一個 technique 的紅藍狀態。"""

    technique_id: str
    name: str
    tactic: str
    note: str | None
    red: RedMark
    blue: BlueMark

    @property
    def counts_toward_coverage(self) -> bool:
        """這一列進不進 action coverage 分母。

        只有 DETECTED（hit）與 MISSED（miss）算 —— 與引擎 `covered = HIT+MISS` 同一條線。
        UNKNOWN（設備故障）與 NOT_APPLICABLE（紅隊沒做）不進分母，否則會冤枉藍隊。
        """
        return self.blue in (BlueMark.DETECTED, BlueMark.MISSED)


def _aggregate_blue(states: list[ActionState]) -> tuple[RedMark, BlueMark]:
    """把一個 technique 底下多筆 action 的四態聚合成一組紅藍顯示狀態。

    優先序 **MISS ＞ HIT ＞ UNKNOWN**：一次 miss 絕不能被同 technique 的另一次 hit 洗掉
    （§3.4 不得漂白缺口）；沒有 miss 時，至少偵測到一次就算 DETECTED；全 unknown 才 UNKNOWN。
    全部 not_executed → 紅隊整個 technique 沒做，藍欄 NOT_APPLICABLE。
    """
    executed = [s for s in states if s is not ActionState.NOT_EXECUTED]
    if not executed:
        return RedMark.NOT_EXECUTED, BlueMark.NOT_APPLICABLE
    if ActionState.MISS in executed:
        blue = BlueMark.MISSED
    elif ActionState.HIT in executed:
        blue = BlueMark.DETECTED
    else:  # 執行了但全部 unknown（來源掉線期間）
        blue = BlueMark.UNKNOWN
    return RedMark.EXECUTED, blue


def build_coverage_matrix(
    outcomes: list[ActionOutcome],
    techniques: dict[str, TechniqueMeta],
) -> list[CoverageRow]:
    """組出 Coverage 表。

    **只列本 scenario 註冊清單（outcomes）涉及的 technique**（AC）—— 不放完整 Enterprise
    Matrix，否則涵蓋率的視覺印象會與實際分母脫節。順序：依 technique id 穩定排序。

    `techniques` 是 techniques.yaml 載入的 metadata（供 name/tactic/note）；outcome 的
    technique 若不在其中，仍列出（用 id 當 name），因為它確實在這場註冊清單裡。
    """
    by_technique: dict[str, list[ActionState]] = {}
    for outcome in outcomes:
        by_technique.setdefault(outcome.technique, []).append(outcome.state)

    rows: list[CoverageRow] = []
    for technique_id in sorted(by_technique):
        red, blue = _aggregate_blue(by_technique[technique_id])
        meta = techniques.get(technique_id)
        rows.append(
            CoverageRow(
                technique_id=technique_id,
                name=meta.name if meta else technique_id,
                tactic=meta.tactic if meta else "",
                note=meta.note if meta else None,
                red=red,
                blue=blue,
            )
        )
    return rows
