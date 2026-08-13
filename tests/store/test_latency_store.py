"""#90 Phase 4：latency 摘要持久化 —— 「重啟後查得到」。

T2（需要 PG）：走真的 timestamptz 與 upsert。摘要往返一趟後每個端點都要對得上，
兩個 mode 必須各自成列不合併。
"""

from datetime import datetime, timedelta, timezone

from purple.evaluation.latency import LatencyRun, summarize_latency
from purple.store.latency import LatencySummaryStore

T0 = datetime(2026, 8, 13, tzinfo=timezone.utc)


def run(seconds, *, mode):
    return LatencyRun(
        action_id=f"{mode}-{seconds}",
        mode=mode,
        executed_at=T0,
        firing_at=T0 + timedelta(seconds=10),
        response_executed_at=T0 + timedelta(seconds=seconds),
        resolved_at=T0 + timedelta(seconds=seconds + 5),
    )


def twenty(mode, base):
    return [run(value, mode=mode) for value in range(base, base + 20)]


def test_summary_survives_a_new_connection(pg_connection):
    """存進去、換一個 store 實例（模擬重啟）再讀，數字不變。"""
    summaries = summarize_latency(twenty("exercise", 11))
    LatencySummaryStore(pg_connection).save_all("ex-1", summaries)

    reloaded = LatencySummaryStore(pg_connection).for_exercise("ex-1")
    assert set(reloaded) == {"exercise"}
    assert reloaded["exercise"].mttr_p50_ms == summaries["exercise"].mttr_p50_ms
    assert reloaded["exercise"].mttr_p95_ms == summaries["exercise"].mttr_p95_ms
    assert reloaded["exercise"].sample_count == 20


def test_two_modes_are_stored_as_separate_rows(pg_connection):
    summaries = summarize_latency(twenty("exercise", 11) + twenty("automatic", 101))
    store = LatencySummaryStore(pg_connection)
    store.save_all("ex-1", summaries)

    reloaded = store.for_exercise("ex-1")
    assert set(reloaded) == {"automatic", "exercise"}
    # 兩個 mode 的分佈不同，證明沒有被合併成單一列。
    assert reloaded["exercise"].mttr_p50_ms != reloaded["automatic"].mttr_p50_ms


def test_recompute_overwrites_the_same_mode_row(pg_connection):
    store = LatencySummaryStore(pg_connection)
    store.save_all("ex-1", summarize_latency(twenty("exercise", 11)))
    store.save_all("ex-1", summarize_latency(twenty("exercise", 101)))

    reloaded = store.for_exercise("ex-1")
    # 只有一列（覆蓋，不累加），且是後一次的分佈。
    assert list(reloaded) == ["exercise"]
    assert reloaded["exercise"].mttr_p50_ms == 100500


def test_missing_summary_reads_back_as_empty_not_error(pg_connection):
    assert LatencySummaryStore(pg_connection).for_exercise("nope") == {}
    assert LatencySummaryStore(pg_connection).get("nope", "exercise") is None


def test_notes_are_restored_from_defaults_not_the_database(pg_connection):
    store = LatencySummaryStore(pg_connection)
    store.save_all("ex-1", summarize_latency(twenty("exercise", 11)))
    reloaded = store.get("ex-1", "exercise")
    assert reloaded.mttd_floor_note == "Grafana eval interval creates an approximately 10s floor"
    assert reloaded.mttr_mode_note == "includes human decision time"
