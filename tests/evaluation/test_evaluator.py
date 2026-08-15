from datetime import datetime, timedelta, timezone

from purple.evaluation.evaluator import (
    ActionEvidence,
    ActionState,
    EvidenceLevel,
    EvaluationService,
)
from purple.registry.source_registry import ScenarioSource, SourceState, evaluate_registry

NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


def registry(*, falco_heartbeat=NOW, alloy_heartbeat=NOW):
    return evaluate_registry(
        "scenario-1",
        [ScenarioSource("falco"), ScenarioSource("alloy")],
        {"falco": falco_heartbeat, "alloy": alloy_heartbeat},
        NOW,
    )


def evidence(action_id, **overrides):
    values = dict(
        action_id=action_id,
        executed_at=NOW - timedelta(seconds=30),
        window_end=NOW - timedelta(seconds=20),
        expected_sources=("falco", "alloy"),
        telemetry_refs=("raw-1",),
        detection_event_ids=(),
        corroborating_sources=(),
        entry_accepted=False,
    )
    values.update(overrides)
    return ActionEvidence(**values)


def test_every_frozen_action_gets_exactly_one_state():
    service = EvaluationService(
        actions=(
            ("a-hit", "T1059"),
            ("a-miss", "T1190"),
            ("a-unknown", "T1059"),
            ("a-idle", "T1005"),
        ),
        source_registry=registry(falco_heartbeat=NOW - timedelta(minutes=5)),
        evidence_by_action={
            "a-hit": evidence(
                "a-hit",
                detection_event_ids=("evt-1",),
                corroborating_sources=("falco", "alloy"),
                entry_accepted=True,
            ),
            "a-miss": evidence("a-miss", telemetry_refs=("raw-2",)),
            "a-unknown": evidence("a-unknown", expected_sources=("falco",)),
        },
    )
    results = {item.action_id: item for item in service.evaluate()}
    assert results["a-hit"].state is ActionState.HIT
    assert results["a-hit"].level is EvidenceLevel.C3
    assert results["a-miss"].state is ActionState.MISS
    assert results["a-miss"].gap == "detection_gap"
    assert results["a-unknown"].state is ActionState.UNKNOWN
    assert "falco" in results["a-unknown"].reason
    assert results["a-idle"].state is ActionState.NOT_EXECUTED


def test_same_technique_actions_are_correlated_only_by_action_id():
    service = EvaluationService(
        actions=(("first", "T1059"), ("second", "T1059")),
        source_registry=registry(),
        evidence_by_action={
            "second": evidence("second", detection_event_ids=("evt-second",)),
        },
    )
    first, second = service.evaluate()
    assert first.state is ActionState.NOT_EXECUTED
    assert second.state is ActionState.HIT
    assert second.event_ids == ("evt-second",)


def test_single_collector_two_events_are_not_c3():
    item = EvaluationService(
        actions=(("a", "T1059"),),
        source_registry=registry(),
        evidence_by_action={
            "a": evidence(
                "a",
                detection_event_ids=("evt-1", "evt-2"),
                corroborating_sources=("falco", "falco"),
                entry_accepted=True,
            )
        },
    ).evaluate()[0]
    assert item.level is EvidenceLevel.C2


def test_t1005_open_does_not_claim_accepted_or_exfiltrated():
    item = EvaluationService(
        actions=(("read", "T1005"),),
        source_registry=registry(),
        evidence_by_action={
            "read": evidence(
                "read",
                detection_event_ids=("evt-read",),
                corroborating_sources=("falco", "alloy"),
                entry_accepted=True,
            )
        },
    ).evaluate()[0]
    assert item.level is EvidenceLevel.C1
    assert "未證明內容外流" in item.reason


def test_source_marks_reflect_registry_state_and_telemetry_presence():
    """#126：畫面二 Telemetry 欄的輸入——每個 expected source 各自的健康度
    （來自 registry）與有沒有這筆 raw 遙測（來自 evidence），不是聚合後的
    單一 gap 分類。falco 掉線、alloy 健康且有這筆事件：兩者互不影響。"""
    service = EvaluationService(
        actions=(("a", "T1059"),),
        source_registry=registry(falco_heartbeat=NOW - timedelta(minutes=5)),
        evidence_by_action={
            "a": evidence(
                "a",
                telemetry_by_source=(("falco", False), ("alloy", True)),
            ),
        },
    )
    marks = {mark.source_id: mark for mark in service.evaluate()[0].source_marks}
    assert marks["falco"].state is SourceState.STALE
    assert marks["falco"].event_present is False
    assert marks["alloy"].state is SourceState.HEALTHY
    assert marks["alloy"].event_present is True


def test_metrics_exclude_unknown_and_not_executed_and_never_fake_zero_percent():
    service = EvaluationService(
        actions=(("unknown", "T1059"), ("idle", "T1005")),
        source_registry=registry(falco_heartbeat=NOW - timedelta(minutes=5)),
        evidence_by_action={
            "unknown": evidence("unknown", expected_sources=("falco",)),
        },
        alert_volume=12,
    )
    metrics = service.metrics()
    assert metrics.action_coverage is None
    assert metrics.confirmation_rate is None
    assert metrics.alert_volume == 12
    assert metrics.excluded_counts == {"unknown": 1, "not_executed": 1}
    assert "detection_rate" not in metrics.as_dict()
