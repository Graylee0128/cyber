"""紅隊執行的 ground truth —— `not_executed` 與動作時間窗的唯一真相來源（#90 Phase 1）。

沒有這張表，P2 分不開兩件事：

    紅隊根本沒跑這個動作        → not_executed（不進分母）
    跑了，但沒換來偵測          → miss（進分母，是藍隊的缺口）

只靠 Core Event 反推是做不到的：兩者在事件流裡長得一模一樣（都是「沒有事件」）。
把它們混起來，等於每次紅隊少跑一個動作就懲罰藍隊一次。

寫入者是攻擊載具（`purple.harness.attacker`）；讀取者是 Evaluation。
資料庫層的外鍵保證這裡只放得下**已註冊**的動作。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import psycopg

#: 動作執行後，多久之內出現的偵測才算「這個動作換來的」。
#: 90s = Grafana 的評估間隔（~10s 下限，見 `evaluation/latency.py`）再加足夠餘裕，
#: 讓一條慢規則不會被誤判成沒偵測到。呼叫端可覆寫，但不得省略成「無限長」——
#: 無限長的窗會把後面動作的偵測算到前面動作頭上。
DEFAULT_DETECTION_WINDOW = timedelta(seconds=90)


class ExecutionError(ValueError):
    """執行紀錄違反契約（時間無時區、動作未註冊、窗口顛倒）。"""


@dataclass(frozen=True)
class ActionExecution:
    """一次紅隊動作的執行事實。

    `window_end` 明確存下來而不是讀取時再由常數推導：窗寬改了以後，舊演練的
    判定必須維持當初的窗，否則歷史報告會在沒有人動過資料的情況下自己改變。
    """

    exercise_id: str
    action_id: str
    executed_at: datetime
    window_end: datetime
    #: 埋進 payload 的可追蹤標記（`harness.attacker.make_marker`）。
    #: 沒有預設值：這是判定 raw 遙測歸屬的唯一依據，漏填必須在型別層就擋下。
    marker: str


def _require_aware(moment: datetime, label: str) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ExecutionError(f"{label} 缺少 timezone。無時區的時間戳不可默默當成 UTC。")


@dataclass
class ActionExecutionStore:
    conn: psycopg.Connection

    def record(
        self,
        exercise_id: str,
        action_id: str,
        executed_at: datetime,
        marker: str,
        *,
        window: timedelta = DEFAULT_DETECTION_WINDOW,
        window_end: datetime | None = None,
    ) -> ActionExecution:
        """記下一次執行。同一場演練的同一個動作重複記錄 → 保留第一次。

        保留第一次而非覆蓋：重試／重送不該讓 `executed_at` 往後漂，那會讓
        MTTD 憑空縮短。

        `marker` 是必填的：見 `action_executions_marker_present` 的理由。
        """
        if not marker.strip():
            raise ExecutionError(
                f"{action_id}: 缺 marker。沒有 marker 就無法判定哪些 raw 遙測屬於"
                f"這個動作，可見性缺口與偵測缺口會分不開。"
            )
        _require_aware(executed_at, "executed_at")
        end = window_end if window_end is not None else executed_at + window
        _require_aware(end, "window_end")
        if end < executed_at:
            raise ExecutionError(f"{action_id}: 動作時間窗顛倒（{executed_at} > {end}）")

        try:
            self.conn.execute(
                "INSERT INTO action_executions "
                "(exercise_id, action_id, executed_at, window_end, marker) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (exercise_id, action_id) DO NOTHING",
                (exercise_id, action_id, executed_at, end, marker),
            )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise ExecutionError(
                f"動作 {action_id!r} 不在 {exercise_id!r} 的 Action Registry 內；"
                f"未註冊的動作不得有執行紀錄（分母只能由凍結清單推導）"
            ) from exc
        return self.get(exercise_id, action_id)

    def get(self, exercise_id: str, action_id: str) -> ActionExecution:
        row = self.conn.execute(
            "SELECT executed_at, window_end, marker FROM action_executions "
            "WHERE exercise_id = %s AND action_id = %s",
            (exercise_id, action_id),
        ).fetchone()
        if row is None:
            raise ExecutionError(f"{exercise_id!r} 沒有動作 {action_id!r} 的執行紀錄")
        return ActionExecution(exercise_id, action_id, row[0], row[1], row[2])

    def for_exercise(self, exercise_id: str) -> dict[str, ActionExecution]:
        """一場演練所有動作的執行紀錄，以 `action_id` 為鍵。

        沒有紀錄的動作**不會**出現在回傳值裡 —— 缺席就是 `not_executed` 的證據，
        補一個預設值進去會把它洗成 `miss`。
        """
        rows = self.conn.execute(
            "SELECT action_id, executed_at, window_end, marker FROM action_executions "
            "WHERE exercise_id = %s ORDER BY executed_at",
            (exercise_id,),
        ).fetchall()
        return {
            action_id: ActionExecution(exercise_id, action_id, executed_at, window_end, marker)
            for action_id, executed_at, window_end, marker in rows
        }
