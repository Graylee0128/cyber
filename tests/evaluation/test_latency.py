from datetime import datetime, timedelta, timezone

import pytest

from purple.evaluation.latency import (
    LatencyAssembler,
    LatencyRun,
    build_latency_run,
    summarize_latency,
)

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


# ── #90 Phase 4：從真實事件組出 LatencyRun ──────────────────────────────────


def _event(observed_at, *, event_id="evt-x", **extra):
    return {"event_id": event_id, "observed_at": observed_at.isoformat(), **extra}


class TestBuildLatencyRun:
    def test_all_four_timestamps_present(self):
        result = build_latency_run(
            "a-1",
            "exercise",
            T0,
            firing=_event(T0 + timedelta(seconds=10)),
            response=_event(T0 + timedelta(seconds=25)),
            resolution=_event(T0 + timedelta(seconds=40)),
        )
        assert result.mttd == timedelta(seconds=10)
        assert result.mttr == timedelta(seconds=15)
        assert result.containment_duration == timedelta(seconds=30)

    def test_attack_stops_without_response_keeps_containment_drops_mttr(self):
        """#21 驗收：攻擊自行停止但沒有 response → containment 有值、MTTR 不可得。"""
        result = build_latency_run(
            "a-1",
            "exercise",
            T0,
            firing=_event(T0 + timedelta(seconds=10)),
            response=None,
            resolution=_event(T0 + timedelta(seconds=40)),
        )
        assert result.mttr is None
        assert result.containment_duration == timedelta(seconds=30)

    def test_no_firing_leaves_every_derived_metric_unavailable(self):
        result = build_latency_run("a-1", "exercise", T0)
        assert result.mttd is None
        assert result.mttr is None
        assert result.containment_duration is None

    def test_naive_timestamp_is_refused(self):
        with pytest.raises(ValueError, match="timezone"):
            build_latency_run(
                "a-1", "exercise", T0, firing={"event_id": "e", "observed_at": "2026-08-13T00:00:10"}
            )


class FakeExecutions:
    def __init__(self, mapping):
        self._mapping = mapping

    def for_exercise(self, exercise_id):
        return self._mapping


class FakeEvents:
    def __init__(self, firings, responses, resolutions):
        self._firings = firings
        self._responses = responses
        self._resolutions = resolutions

    def firings_by_action(self, exercise_id):
        return self._firings

    def responses_by_attack_event(self, exercise_id):
        return self._responses

    def resolutions_by_event(self, exercise_id):
        return self._resolutions


class _Exec:
    def __init__(self, executed_at):
        self.executed_at = executed_at


class TestLatencyAssembler:
    def test_correlation_walks_event_id_not_time_proximity(self):
        firing = _event(T0 + timedelta(seconds=10), event_id="evt-fire")
        assembler = LatencyAssembler(
            executions=FakeExecutions({"a-1": _Exec(T0)}),
            events=FakeEvents(
                firings={"a-1": firing},
                # response 掛在攻擊 event_id 上，不是憑時間最接近
                responses={"evt-fire": _event(T0 + timedelta(seconds=25), event_id="evt-resp")},
                resolutions={"evt-fire": _event(T0 + timedelta(seconds=40), event_id="evt-fire")},
            ),
            mode_of=lambda _a: "exercise",
        )
        run_result = assembler.build("ex-1")[0]
        assert run_result.mttd == timedelta(seconds=10)
        assert run_result.mttr == timedelta(seconds=15)
        assert run_result.containment_duration == timedelta(seconds=30)

    def test_action_without_firing_gets_no_response_or_resolution(self):
        """沒有 firing 就沒有 join key —— 不得去撈別的動作的 response 硬湊。"""
        assembler = LatencyAssembler(
            executions=FakeExecutions({"a-1": _Exec(T0)}),
            events=FakeEvents(
                firings={},
                responses={"evt-other": _event(T0 + timedelta(seconds=25))},
                resolutions={"evt-other": _event(T0 + timedelta(seconds=40))},
            ),
            mode_of=lambda _a: "exercise",
        )
        run_result = assembler.build("ex-1")[0]
        assert run_result.firing_at is None
        assert run_result.mttr is None
        assert run_result.containment_duration is None

    def test_mode_comes_from_the_resolver(self):
        assembler = LatencyAssembler(
            executions=FakeExecutions({"a-1": _Exec(T0), "a-2": _Exec(T0)}),
            events=FakeEvents({}, {}, {}),
            mode_of=lambda action_id: "automatic" if action_id == "a-2" else "exercise",
        )
        runs = {r.action_id: r.mode for r in assembler.build("ex-1")}
        assert runs == {"a-1": "exercise", "a-2": "automatic"}

    def test_assembled_runs_summarize_and_keep_modes_separate(self):
        executions = {f"a-{i}": _Exec(T0) for i in range(40)}
        firings = {
            f"a-{i}": _event(T0 + timedelta(seconds=10), event_id=f"evt-{i}") for i in range(40)
        }
        responses = {
            f"evt-{i}": _event(T0 + timedelta(seconds=11 + (i % 20)), event_id=f"resp-{i}")
            for i in range(40)
        }
        assembler = LatencyAssembler(
            executions=FakeExecutions(executions),
            events=FakeEvents(firings, responses, {}),
            mode_of=lambda action_id: "automatic" if int(action_id.split("-")[1]) >= 20 else "exercise",
        )
        summaries = summarize_latency(assembler.build("ex-1"))
        assert summaries["exercise"].sample_count == 20
        assert summaries["automatic"].sample_count == 20
