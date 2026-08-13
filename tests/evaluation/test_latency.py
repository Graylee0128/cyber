from datetime import datetime, timedelta, timezone

import pytest

from purple.evaluation.latency import LatencyRun, summarize_latency

T0 = datetime(2026, 8, 13, tzinfo=timezone.utc)


def run(seconds, *, mode="exercise", response=True, resolved=True):
    return LatencyRun(
        action_id=f"a-{seconds}",
        mode=mode,
        executed_at=T0,
        firing_at=T0 + timedelta(seconds=10),
        response_executed_at=(T0 + timedelta(seconds=seconds) if response else None),
        resolved_at=(T0 + timedelta(seconds=seconds + 5) if resolved else None),
    )


def test_attack_can_stop_without_faking_mttr():
    result = run(30, response=False)
    assert result.mttd == timedelta(seconds=10)
    assert result.mttr is None
    assert result.containment_duration == timedelta(seconds=25)


def test_exactly_twenty_runs_produce_p50_p95_and_keep_modes_separate():
    exercise = [run(value, mode="exercise") for value in range(11, 31)]
    automatic = [run(value, mode="automatic") for value in range(101, 121)]
    summaries = summarize_latency(exercise + automatic)
    assert summaries["exercise"].sample_count == 20
    assert summaries["exercise"].mttr_p50_ms == 10500
    assert summaries["exercise"].mttr_p95_ms == 19000
    assert summaries["automatic"].mttr_p50_ms == 100500
    assert summaries["exercise"].mttd_floor_note == "Grafana eval interval creates an approximately 10s floor"
    assert summaries["exercise"].mttr_mode_note == "includes human decision time"


def test_nineteen_runs_are_not_accepted_as_final_measurement():
    with pytest.raises(ValueError, match="exactly 20"):
        summarize_latency([run(value) for value in range(11, 30)])
