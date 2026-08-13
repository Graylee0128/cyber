"""#26 API adapter：Console 讀 Evaluation API JSON → view model，不自算比率。"""

from purple.console.adapter import coverage_from_api, coverage_metrics
from purple.console.coverage import BlueMark, RedMark, TechniqueMeta

TECHS = {
    "T1190": TechniqueMeta("T1190", "Exploit Public-Facing Application", "initial-access"),
    "T1059": TechniqueMeta("T1059", "Command and Scripting Interpreter", "execution"),
}

# render_evaluation / get_registry 的真實輸出形狀。
REGISTRY = {"actions": [
    {"action_id": "a1", "technique": "T1190", "description": "exploit"},
    {"action_id": "a2", "technique": "T1059", "description": "shell"},
]}
EVALUATION = {
    "metrics": {"action_coverage": None, "confirmation_rate": None,
                "excluded": {"unknown": 1, "not_executed": 0}},
    "actions": [
        {"action_id": "a1", "state": "hit", "level": "C1", "gap": None, "reason": "", "event_ids": []},
        {"action_id": "a2", "state": "unknown", "level": None, "gap": None, "reason": "", "event_ids": []},
    ],
}


def test_coverage_joins_registry_technique_with_evaluation_state():
    rows = coverage_from_api(registry=REGISTRY, evaluation=EVALUATION, techniques=TECHS)
    by_id = {r.technique_id: r for r in rows}
    assert by_id["T1190"].red == RedMark.EXECUTED and by_id["T1190"].blue == BlueMark.DETECTED
    assert by_id["T1059"].blue == BlueMark.UNKNOWN


def test_metrics_are_passed_through_untouched():
    # Console 不自算比率：分母為空時原封傳 API 的 null，不改成 0。
    metrics = coverage_metrics(EVALUATION)
    assert metrics["action_coverage"] is None
    assert metrics["excluded"]["unknown"] == 1


def test_evaluation_action_without_registered_technique_is_skipped():
    evaluation = {"actions": [{"action_id": "ghost", "state": "hit"}]}
    rows = coverage_from_api(registry=REGISTRY, evaluation=evaluation, techniques=TECHS)
    assert rows == []
