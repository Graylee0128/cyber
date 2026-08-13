"""Latency 摘要的持久化（#90 Phase 4）—— 「重啟後查得到」的那一層。

存的是 `LatencySummary`（每個 mode 一列的 p50/p95），不是原始 `LatencyRun`。
理由見 `db.py` 的 `latency_summaries` 註解：報告要的是分佈端點，不是可重算的原始樣本。

`mttd_floor_note` / `mttr_mode_note` 不入表 —— 它們是常數，由 `LatencySummary` 的
預設值在讀取時補回。把常數寫進每一列只會製造「某天某列的註記和別列不一樣」的假象。
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from purple.evaluation.latency import LatencySummary

_COLUMNS = (
    "mode",
    "sample_count",
    "mttd_p50_ms",
    "mttd_p95_ms",
    "mttr_p50_ms",
    "mttr_p95_ms",
    "containment_p50_ms",
    "containment_p95_ms",
)

_UPSERT = """
INSERT INTO latency_summaries
    (exercise_id, mode, sample_count,
     mttd_p50_ms, mttd_p95_ms, mttr_p50_ms, mttr_p95_ms,
     containment_p50_ms, containment_p95_ms, computed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (exercise_id, mode) DO UPDATE SET
    sample_count = EXCLUDED.sample_count,
    mttd_p50_ms = EXCLUDED.mttd_p50_ms,
    mttd_p95_ms = EXCLUDED.mttd_p95_ms,
    mttr_p50_ms = EXCLUDED.mttr_p50_ms,
    mttr_p95_ms = EXCLUDED.mttr_p95_ms,
    containment_p50_ms = EXCLUDED.containment_p50_ms,
    containment_p95_ms = EXCLUDED.containment_p95_ms,
    computed_at = now()
"""


@dataclass
class LatencySummaryStore:
    conn: psycopg.Connection

    def save(self, exercise_id: str, summary: LatencySummary) -> None:
        """存一個 mode 的摘要。重算後覆蓋同 (exercise, mode) 那一列。

        覆蓋而非累加：一場演練的一個 mode 只有一份最終分佈，重跑就是取代。
        """
        self.conn.execute(
            _UPSERT,
            (
                exercise_id,
                summary.mode,
                summary.sample_count,
                summary.mttd_p50_ms,
                summary.mttd_p95_ms,
                summary.mttr_p50_ms,
                summary.mttr_p95_ms,
                summary.containment_p50_ms,
                summary.containment_p95_ms,
            ),
        )

    def save_all(self, exercise_id: str, summaries: dict[str, LatencySummary]) -> None:
        for summary in summaries.values():
            self.save(exercise_id, summary)

    def get(self, exercise_id: str, mode: str) -> LatencySummary | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM latency_summaries "
            "WHERE exercise_id = %s AND mode = %s",
            (exercise_id, mode),
        ).fetchone()
        return _row_to_summary(row) if row is not None else None

    def for_exercise(self, exercise_id: str) -> dict[str, LatencySummary]:
        """一場演練所有 mode 的摘要，以 mode 為鍵。沒有任何摘要時回空 dict。"""
        rows = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM latency_summaries "
            "WHERE exercise_id = %s ORDER BY mode",
            (exercise_id,),
        ).fetchall()
        return {row[0]: _row_to_summary(row) for row in rows}


def _row_to_summary(row: tuple) -> LatencySummary:
    # note_ 欄位刻意不從 DB 取，由 LatencySummary 預設值補回（見模組 docstring）。
    return LatencySummary(
        mode=row[0],
        sample_count=row[1],
        mttd_p50_ms=row[2],
        mttd_p95_ms=row[3],
        mttr_p50_ms=row[4],
        mttr_p95_ms=row[5],
        containment_p50_ms=row[6],
        containment_p95_ms=row[7],
    )
