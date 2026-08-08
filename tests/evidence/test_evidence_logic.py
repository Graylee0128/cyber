"""Evidence 的純函數 —— 窗計算、逐行過濾、後端契約。

秒級、不碰儲存。（整套仍受 conftest 的 PG autouse 前置檢查約束，見 map.md
測試性質分級：06 為「半」—— 邏輯可先測，整合需 docker。）
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.evidence.backends import (
    BackendUnavailable,
    ContextLine,
    EvidenceQuery,
    FakeBackend,
    LokiBackend,
)
from purple.evidence.resolver import (
    DEFAULT_WINDOW,
    Caller,
    UnknownCaller,
    build_query,
    clearance,
    filter_by_visibility,
)

TZ = timezone(timedelta(hours=8))
OBSERVED_AT = datetime(2026, 8, 8, 14, 30, tzinfo=TZ)

RECORD = {
    "event_id": "evt-1",
    "grafana_rule": "SQLInjectionBurst",
    "query": '{app="x"} |= "OR 1=1"',
    "threshold": "> 4 / 1m",
    "fired_values": [{"A": 12}],
    "labels": {"service": "vulnerable-app", "scenario_id": "sqli-01"},
    "backend": "loki",
}
REFERENCE = {"event_id": "evt-1", "observed_at": OBSERVED_AT.isoformat()}


def _line(minute: int, visibility: str = "public") -> ContextLine:
    return ContextLine(
        timestamp=OBSERVED_AT.replace(minute=minute),
        line=f"log at :{minute:02d} [{visibility}]",
        visibility=visibility,
    )


class TestBuildQuery:
    def test_window_straddles_observed_at(self):
        q = build_query(RECORD, REFERENCE)
        assert q.start == OBSERVED_AT - DEFAULT_WINDOW
        assert q.end == OBSERVED_AT + DEFAULT_WINDOW
        assert q.start < OBSERVED_AT < q.end

    def test_window_width_is_configurable(self):
        q = build_query(RECORD, REFERENCE, window=timedelta(minutes=1))
        assert q.end - q.start == timedelta(minutes=2)

    def test_source_prefers_explicit_source_label(self):
        rec = {**RECORD, "labels": {"source": "application-log", "service": "vulnerable-app"}}
        assert build_query(rec, REFERENCE).source == "application-log"

    def test_source_falls_back_to_service(self):
        assert build_query(RECORD, REFERENCE).source == "vulnerable-app"

    def test_naive_observed_at_is_rejected(self):
        naive = {"observed_at": "2026-08-08T14:30:00"}  # 無時區
        with pytest.raises(Exception):
            build_query(RECORD, naive)

    def test_query_object_carries_no_caller_supplied_text(self):
        """build_query 只吃兩份紀錄 ＋ 窗寬，沒有讓呼叫者塞查詢語法的入口。"""
        q = build_query(RECORD, REFERENCE)
        assert isinstance(q, EvidenceQuery)
        assert q.labels["service"] == "vulnerable-app"


class TestFilterByVisibility:
    ALL = (
        _line(28, "public"),
        _line(29, "blue"),
        _line(30, "purple"),
        _line(31, "instructor"),
    )

    def test_red_sees_public_only(self):
        got = filter_by_visibility(self.ALL, Caller("red"))
        assert [l.visibility for l in got] == ["public"]

    def test_blue_sees_public_and_blue(self):
        got = filter_by_visibility(self.ALL, Caller("blue"))
        assert [l.visibility for l in got] == ["public", "blue"]

    def test_purple_sees_up_to_purple(self):
        got = filter_by_visibility(self.ALL, Caller("purple"))
        assert [l.visibility for l in got] == ["public", "blue", "purple"]

    def test_instructor_sees_everything(self):
        got = filter_by_visibility(self.ALL, Caller("instructor"))
        assert len(got) == 4

    def test_unknown_caller_raises_not_defaults_open(self):
        """未知身分絕不預設成看得到全部 —— fail loud。"""
        with pytest.raises(UnknownCaller):
            filter_by_visibility(self.ALL, Caller("ghost"))

    def test_unknown_visibility_fails_closed(self):
        """未知 visibility 當成最嚴格，只有 instructor 看得到。"""
        weird = (ContextLine(OBSERVED_AT, "??", visibility="top-secret"),)
        assert filter_by_visibility(weird, Caller("purple")) == ()
        assert len(filter_by_visibility(weird, Caller("instructor"))) == 1


class TestClearance:
    def test_ordering_is_linear(self):
        assert (
            clearance(Caller("red"))
            < clearance(Caller("blue"))
            < clearance(Caller("purple"))
            < clearance(Caller("instructor"))
        )

    def test_unknown_identity_raises(self):
        with pytest.raises(UnknownCaller):
            clearance(Caller("nobody"))


class TestFakeBackend:
    def test_returns_only_lines_inside_the_window(self):
        lines = (_line(20), _line(30), _line(40))  # 20 與 40 在 ±5m 窗外
        q = build_query(RECORD, REFERENCE)  # 14:25–14:35
        got = FakeBackend(lines).fetch_context(q)
        assert [l.timestamp.minute for l in got] == [30]


class TestLokiBackend:
    def test_stub_fails_loud_never_fakes_data(self):
        """本環境沒有 Loki。佔位實作寧可明確炸，也不回假上下文。"""
        with pytest.raises(BackendUnavailable):
            LokiBackend().fetch_context(build_query(RECORD, REFERENCE))
