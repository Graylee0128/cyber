"""Evidence resolver —— 對真 PG（Alert Record ＋ Core Event）＋ 可插拔 backend。

這裡的頭兩條是 ticket 的紅燈：
- `returns_context_window_not_single_line`
- `visibility_filter_applied_per_caller`

其餘證明架構決策：backend 只住 Alert Record、resolver 不吃查詢語法、換 backend
不動 Core Event Schema。
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from purple.evidence import (
    BackendUnavailable,
    Caller,
    ContextLine,
    EvidenceNotFound,
    EvidenceResolver,
    FakeBackend,
    LokiBackend,
)
from purple.harness.schema import assert_core_event
from purple.store.alerts import AlertRecordStore
from purple.store.events import CoreEventStore

TZ = timezone(timedelta(hours=8))
OBSERVED_AT = datetime(2026, 8, 8, 14, 30, tzinfo=TZ)
EVENT_ID = "evt-resolver-0001"

CORE_EVENT = {
    "event_id": EVENT_ID,
    "exercise_id": "ex-001",
    "scenario_id": "sqli-01",
    "event_type": "attack.detected",
    "lifecycle": "firing",
    "severity": "high",
    "source": "grafana",
    "team": "red",
    "technique": "T1190",
    "target": {"service": "vulnerable-app"},
    "observed_at": OBSERVED_AT.isoformat(),
    "visibility": "public",
    "action_id": None,
}

ALERT_RECORD = {
    "event_id": EVENT_ID,
    "grafana_rule": "SQLInjectionBurst",
    "query": '{app="vulnerable-app"} |= "OR 1=1"',
    "threshold": "> 4 / 1m",
    "fired_values": [{"A": 12}],
    "labels": {"service": "vulnerable-app", "scenario_id": "sqli-01"},
    "backend": "loki",
}


def _line(minute: int, visibility: str = "public") -> ContextLine:
    return ContextLine(
        timestamp=OBSERVED_AT.replace(minute=minute),
        line=f"req at 14:{minute:02d} [{visibility}]",
        visibility=visibility,
    )


#: 事件前後各數行，含攻擊那一刻（:30）。分析師要看的是周遭發生什麼。
CONTEXT = (
    _line(28, "public"),
    _line(29, "public"),
    _line(30, "public"),
    _line(31, "blue"),
    _line(32, "purple"),
)


@pytest.fixture
def stores(pg_connection):
    return CoreEventStore(pg_connection), AlertRecordStore(pg_connection)


@pytest.fixture
def seeded(stores):
    events, records = stores
    records.write(ALERT_RECORD)  # Alert Record 先落地（spec §2.5）
    events.append(CORE_EVENT)
    return events, records


def _resolver(seeded, backend):
    events, records = seeded
    return EvidenceResolver(records=records, events=events, backend=backend)


class TestRedLights:
    def test_returns_context_window_not_single_line(self, seeded):
        """回傳事件前後的上下文窗，不是孤立一行。"""
        resolver = _resolver(seeded, FakeBackend(CONTEXT))
        bundle = resolver.resolve(EVENT_ID, caller=Caller("purple"))

        assert bundle.line_count > 1, "上下文窗不該只有一行"
        before = [l for l in bundle.lines if l.timestamp < OBSERVED_AT]
        after = [l for l in bundle.lines if l.timestamp > OBSERVED_AT]
        assert before, "缺少事件之前的上下文"
        assert after, "缺少事件之後的上下文"

    def test_visibility_filter_applied_per_caller(self, seeded):
        """不同身分取得不同內容。"""
        resolver = _resolver(seeded, FakeBackend(CONTEXT))

        red = resolver.resolve(EVENT_ID, caller=Caller("red"))
        blue = resolver.resolve(EVENT_ID, caller=Caller("blue"))
        purple = resolver.resolve(EVENT_ID, caller=Caller("purple"))

        # 逐級放寬：red ⊂ blue ⊂ purple
        assert red.line_count < blue.line_count < purple.line_count
        assert all(l.visibility == "public" for l in red.lines)
        assert {l.visibility for l in purple.lines} == {"public", "blue", "purple"}
        # 內容確實不同，不只是數量
        assert {l.line for l in red.lines} != {l.line for l in purple.lines}


class TestArchitectureBoundaries:
    def test_backend_lives_only_in_alert_record_not_in_core_event(self, seeded):
        events, records = seeded
        assert records.by_id(EVENT_ID)["backend"] == "loki"

        core = events.by_id(EVENT_ID)[0]
        assert "backend" not in core
        assert_core_event(core)  # schema 斷言本身就拒 backend 欄位

    def test_bundle_does_not_leak_backend(self, seeded):
        resolver = _resolver(seeded, FakeBackend(CONTEXT))
        bundle = resolver.resolve(EVENT_ID, caller=Caller("purple"))
        assert not hasattr(bundle, "backend")
        assert "backend" not in repr(bundle).lower()

    def test_resolve_takes_only_event_id_and_caller_no_query_language(self):
        """resolver 以 event_id 為唯一資料輸入，不接受呼叫端傳入查詢語法。"""
        params = set(inspect.signature(EvidenceResolver.resolve).parameters)
        assert params == {"self", "event_id", "caller"}
        for banned in ("query", "logql", "promql", "loki", "expr", "selector"):
            assert banned not in params


class TestSwappableBackend:
    def test_swapping_backend_changes_nothing_but_the_backend(self, seeded):
        """換 backend（Fake→另一個 Fake）resolver 行為一致，Core Event 一個字不動。

        這是決定性設計測試：resolver 只依賴 EvidenceBackend interface，
        換掉 Loki 只需換這個實作，Core Event Schema 不動（ADR ④）。
        """
        events, _ = seeded
        core_before = events.by_id(EVENT_ID)[0]

        backend_a = FakeBackend((_line(29, "public"), _line(31, "public")))
        backend_b = FakeBackend((_line(28, "public"), _line(30, "public"), _line(32, "public")))

        a = _resolver(seeded, backend_a).resolve(EVENT_ID, caller=Caller("purple"))
        b = _resolver(seeded, backend_b).resolve(EVENT_ID, caller=Caller("purple"))

        # 同一套 resolver 邏輯，只有 backend 回的內容不同
        assert a.line_count == 2
        assert b.line_count == 3
        assert a.window_start == b.window_start and a.window_end == b.window_end

        # 換 backend 前後，Core Event 完全沒被碰過
        core_after = events.by_id(EVENT_ID)[0]
        assert core_after == core_before
        assert_core_event(core_after)

    def test_loki_backend_is_a_drop_in_that_this_env_cannot_serve(self, seeded):
        """LokiBackend 能被插進同一個 resolver —— 只是本環境沒有 Loki，明確炸。

        它「插得進去卻炸在 backend 層」證明 resolver 呼叫的是介面，不是
        Loki 專屬程式碼；接上真 Loki 後 resolver 一行都不用改。
        """
        resolver = _resolver(seeded, LokiBackend())
        with pytest.raises(BackendUnavailable):
            resolver.resolve(EVENT_ID, caller=Caller("purple"))


class TestMissing:
    def test_unknown_event_id_raises_not_found(self, seeded):
        resolver = _resolver(seeded, FakeBackend(CONTEXT))
        with pytest.raises(EvidenceNotFound):
            resolver.resolve("evt-does-not-exist", caller=Caller("purple"))

    def test_alert_record_without_core_event_raises_not_found(self, stores):
        events, records = stores
        records.write(ALERT_RECORD)  # 只有 Alert Record，沒有 Core Event
        resolver = EvidenceResolver(records=records, events=events, backend=FakeBackend(CONTEXT))
        with pytest.raises(EvidenceNotFound):
            resolver.resolve(EVENT_ID, caller=Caller("purple"))
