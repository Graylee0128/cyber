"""Exercise Report —— P2 的收尾產出（票 #28）。

即時畫面演練一結束就消失，報告會被轉寄、被引用、被拿去要預算 —— **這是紫隊唯一會被
帶走的東西**。所以三個欄位少一個都會誤導讀者，這裡用型別把它們釘成不可省：

1. **告警總量與 coverage 並列**（BlueSummary 同時要 coverage 與 alert_volume）——
   少了會獎勵「對所有東西告警」（§3.2 陷阱②）。
2. **每個 coverage gap 標註是偵測缺口還是可見性缺口**（CoverageGap 強制帶 MissClass）——
   混講會冤枉藍隊、改善方向也失焦（§3.4）。
3. **unknown 的數量與原因**（UnknownSummary 在 count>0 時強制要 reasons）——
   少了會把設備故障算在藍隊頭上，或讓它從分母悄悄消失（§3.5）。

**數字一律取自 Evaluation API 的 metrics，報告不重算**（AC）。報告是 frozen dataclass，
產出當下把數字複製進來，之後 metrics 再變也不動 —— 快照後不隨後續資料變動。
`detection_rate` 這個歷史別名（ADR ⑨）在整份報告的任何一層都不得出現。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from purple.metrics.gaps import MissClass
from purple.retention.window import RetentionWindow, raw_coverage_windows_for

#: 只有這兩種才是「缺口」。hit 不是缺口，unknown／out_of_scope 不進缺口清單。
_REAL_GAPS = (MissClass.DETECTION_GAP, MissClass.VISIBILITY_GAP)


class ReportContractError(ValueError):
    """報告缺了不可省的欄位，或帶了被禁止的欄位。"""


@dataclass(frozen=True)
class CoverageGap:
    """一個 coverage gap，**必須**標註哪一種缺口。"""

    technique: str
    classification: MissClass

    def __post_init__(self) -> None:
        if self.classification not in _REAL_GAPS:
            raise ReportContractError(
                f"coverage gap {self.technique} 的分類必須是偵測缺口或可見性缺口，"
                f"不能是 {self.classification!r}（混講會冤枉藍隊，§3.4）"
            )


@dataclass(frozen=True)
class UnknownSummary:
    """unknown 的數量**與**原因。count>0 卻沒有原因是不允許的。"""

    count: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.count > 0 and not self.reasons:
            raise ReportContractError(
                f"unknown={self.count} 卻沒有原因字串 —— 把設備故障寫成一個裸數字，"
                f"讀者無從分辨是藍隊漏了還是遙測掉線（§3.5）"
            )


@dataclass(frozen=True)
class RedSummary:
    attack_success_pct: int
    objectives_completed: int
    objectives_total: int


@dataclass(frozen=True)
class BlueSummary:
    """coverage 與 alert_volume **同時**在場 —— 沒有只吐 coverage 的建構路徑。"""

    action_coverage: float | None
    alert_volume: int
    mttd_ms: int | None
    mttr_ms: int | None


@dataclass(frozen=True)
class ExerciseReport:
    exercise_id: str
    red: RedSummary
    blue: BlueSummary
    coverage_gaps: tuple[CoverageGap, ...]
    unknown: UnknownSummary
    #: 哪些時段有 raw 覆蓋（§4.2）—— retention 過期時標明範圍，而不是靜默顯示空白。
    raw_coverage_windows: tuple[str, ...]
    recommendations: tuple[str, ...]
    #: #131／#132：AI 生成的敘事摘要，純呈現層——不是新的判斷來源，只是把上面
    #: 這些已經凍結的數字唸成一段話。預設 None：舊呼叫端不受影響，Ollama 不可用
    #: 時報告照樣產出，只是沒有這段（見 purple.report.narrative）。
    narrative: str | None = None

    def as_dict(self) -> dict[str, object]:
        report = {
            "exercise_id": self.exercise_id,
            "red": {
                "attack_success_pct": self.red.attack_success_pct,
                "objectives": f"{self.red.objectives_completed}/{self.red.objectives_total}",
            },
            "blue": {
                "action_coverage": self.blue.action_coverage,
                "alert_volume": self.blue.alert_volume,
                "mttd_ms": self.blue.mttd_ms,
                "mttr_ms": self.blue.mttr_ms,
            },
            "coverage_gaps": [
                {"technique": g.technique, "classification": g.classification.value}
                for g in self.coverage_gaps
            ],
            "unknown": {"count": self.unknown.count, "reasons": list(self.unknown.reasons)},
            "raw_coverage_windows": list(self.raw_coverage_windows),
            "recommendations": list(self.recommendations),
            "narrative": self.narrative,
        }
        _reject_detection_rate(report)
        return report


def _reject_detection_rate(node: object) -> None:
    """遞迴確認整份報告任何一層都沒有 detection_rate 別名（ADR ⑨）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.replace(" ", "_").lower() == "detection_rate":
                raise ReportContractError("報告不得出現 Detection Rate（ADR ⑨ 的歷史別名）")
            _reject_detection_rate(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _reject_detection_rate(item)


def build_exercise_report(
    *,
    exercise_id: str,
    metrics: dict[str, object],
    red: RedSummary,
    coverage_gaps: tuple[CoverageGap, ...],
    unknown_reasons: tuple[str, ...],
    mttd_ms: int | None,
    mttr_ms: int | None,
    retention_window: RetentionWindow,
    event_timestamps: Iterable[datetime] = (),
    recommendations: tuple[str, ...] = (),
    narrative: str | None = None,
) -> ExerciseReport:
    """從 Evaluation API 的 `metrics` 快照組出報告 —— 數字複製進來，不重算。

    `metrics` 必須帶 `alert_volume` 與 `excluded_counts.unknown`；缺了就是上游 API 形狀
    不對，寧可 fail loud 也不要生一份缺欄位的報告。

    `raw_coverage_windows`（票 #98 項目 3）不再由呼叫端手填字串 —— 傳
    `retention_window` 與報告引用到的 `event_timestamps`，過期判定由
    `raw_coverage_windows_for` 自動算出，不必倚賴呼叫端手填、也不會靜默
    留空。

    `narrative`（#131／#132）刻意維持這個函式本身無 I/O：呼叫端要先組出報告、
    用 `.as_dict()` 餵給 `purple.report.narrative.generate_narrative()`（那裡才會
    真的打 Ollama），再把結果傳回這裡，或用 `dataclasses.replace()` 事後貼上去。
    不在這裡直接呼叫 AI，是不想讓「組報告」這個純函數背上一次網路呼叫的延遲與
    失敗模式。
    """
    if "alert_volume" not in metrics:
        raise ReportContractError("metrics 缺 alert_volume —— 告警總量必須與 coverage 並列")
    excluded = metrics.get("excluded_counts") or {}
    unknown_count = int(excluded.get("unknown", 0))

    blue = BlueSummary(
        action_coverage=metrics.get("action_coverage"),  # type: ignore[arg-type]
        alert_volume=int(metrics["alert_volume"]),
        mttd_ms=mttd_ms,
        mttr_ms=mttr_ms,
    )
    return ExerciseReport(
        exercise_id=exercise_id,
        red=red,
        blue=blue,
        coverage_gaps=tuple(coverage_gaps),
        unknown=UnknownSummary(count=unknown_count, reasons=tuple(unknown_reasons)),
        raw_coverage_windows=raw_coverage_windows_for(retention_window, event_timestamps),
        recommendations=tuple(recommendations),
        narrative=narrative,
    )
