"""#90 Phase 1：紅隊執行 ground truth。`not_executed` 與 `miss` 的分界線。"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.evaluation.action_registry import ActionRegistryStore, RegisteredAction
from purple.receiver.whitelist import default_whitelist
from purple.store.executions import ActionExecutionStore, ExecutionError

NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
MARKER = "-- purple:abc action:a-1"


@pytest.fixture
def registry(pg_connection):
    store = ActionRegistryStore(pg_connection, default_whitelist())
    store.seed(
        "ex-1",
        "sqli-01",
        [
            RegisteredAction("a-1", "T1190", "exploit app"),
            RegisteredAction("a-2", "T1059", "run shell"),
        ],
    )
    return store


@pytest.fixture
def executions(pg_connection, registry):
    return ActionExecutionStore(pg_connection)


def test_unregistered_action_cannot_have_an_execution(executions):
    """分母只能由凍結清單推導 —— 孤兒執行紀錄是反推分母的後門。"""
    with pytest.raises(ExecutionError, match="Action Registry"):
        executions.record("ex-1", "a-never-registered", NOW, MARKER)


def test_marker_is_required(executions):
    with pytest.raises(ExecutionError, match="marker"):
        executions.record("ex-1", "a-1", NOW, "   ")


def test_naive_timestamp_is_refused(executions):
    with pytest.raises(ExecutionError, match="timezone"):
        executions.record("ex-1", "a-1", datetime(2026, 8, 13, 4, 0), MARKER)


def test_inverted_window_is_refused(executions):
    with pytest.raises(ExecutionError, match="顛倒"):
        executions.record(
            "ex-1", "a-1", NOW, MARKER, window_end=NOW - timedelta(seconds=1)
        )


def test_window_end_defaults_to_the_detection_window(executions):
    recorded = executions.record("ex-1", "a-1", NOW, MARKER)
    assert recorded.window_end - recorded.executed_at == timedelta(seconds=90)


def test_replay_keeps_the_first_execution_time(executions):
    """重送不得讓 executed_at 往後漂 —— 那會讓 MTTD 憑空縮短。"""
    executions.record("ex-1", "a-1", NOW, MARKER)
    later = executions.record("ex-1", "a-1", NOW + timedelta(seconds=30), MARKER)
    assert later.executed_at == NOW


def test_actions_without_an_execution_are_absent_not_blank(executions):
    """缺席就是 not_executed 的證據；補一筆空值會把它洗成 miss。"""
    executions.record("ex-1", "a-1", NOW, MARKER)
    found = executions.for_exercise("ex-1")
    assert set(found) == {"a-1"}
    assert "a-2" not in found
