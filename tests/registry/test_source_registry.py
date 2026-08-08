"""Source Registry 狀態機的純函數層 —— 真 TDD，不需要 docker，秒級。

票 07 的理由（spec §3.1、ADR ⑧）：把「來源有裝但掉線」與「來源根本沒裝」
混為一談，會系統性冤枉藍隊。掉線＝stale（算缺口），沒裝＝absent（只標範圍）。

註：整個 suite 由 conftest 的 autouse fixture 要求 PostgreSQL，故 CI 會連 PG；
但本模組是純函數、完全不碰 DB，邏輯正確性與 PG 無關。
"""

from datetime import datetime, timedelta, timezone

import pytest

from purple.registry.source_registry import (
    HEARTBEAT_INTERVAL_S,
    HEARTBEAT_TOLERANCE_S,
    Registry,
    ScenarioSource,
    SourceState,
    StaleInterval,
    evaluate_registry,
)

NOW = datetime(2026, 8, 8, 15, 0, 0, tzinfo=timezone.utc)


def beats_ago(seconds: float) -> datetime:
    """seconds 秒前的一次 heartbeat（aware）。"""
    return NOW - timedelta(seconds=seconds)


def evaluate(sources, heartbeats, now=NOW, **kwargs) -> Registry:
    return evaluate_registry("sqli-01", sources, heartbeats, now, **kwargs)


class TestThreeStates:
    """spec §3.3 狀態表的三條紅燈。`expired_heartbeat_is_stale_not_absent`
    是整張票的理由。"""

    def test_expired_heartbeat_is_stale_not_absent(self):
        """整張票的核心。殺掉 Falco → heartbeat 逾時 → state 必須是 stale。

        「實測：殺掉 Falco 後狀態轉 stale」需要真的 Falco，本機沒有；
        對真實 Falco 的 live 斷言歸環境票 #13。這裡餵一個逾時的
        heartbeat 時間戳，斷言 stale（**不是** absent）—— 掉線的來源
        絕不能被洗成「不在範圍內」而免責（ADR ⑧⑫）。
        """
        registry = evaluate(
            [ScenarioSource("falco", expected=True)],
            {"falco": beats_ago(HEARTBEAT_TOLERANCE_S + 60)},
        )
        falco = registry.get("falco")
        assert falco.state is SourceState.STALE
        assert falco.state is not SourceState.ABSENT

    def test_heartbeat_within_tolerance_is_healthy(self):
        registry = evaluate(
            [ScenarioSource("application-log", expected=True)],
            {"application-log": beats_ago(2)},
        )
        assert registry.get("application-log").state is SourceState.HEALTHY

    def test_not_expected_is_absent(self):
        registry = evaluate(
            [ScenarioSource("waf", expected=False)],
            {},
        )
        waf = registry.get("waf")
        assert waf.state is SourceState.ABSENT
        assert waf.last_heartbeat is None


class TestExpectedComesFromScenarioNotDeployment:
    """ADR ⑧ 的結構性要求：`expected` 來自 scenario 定義，不從部署／heartbeat 推導。
    掉線的來源必須留在清單裡（判 stale），不可從自動推導結果中消失。"""

    def test_crashed_source_stays_in_registry_as_stale(self):
        """falco 崩潰、完全沒 heartbeat。若靠部署推導它會消失＝被洗成免責；
        這裡它必須仍在 registry 且為 stale。"""
        registry = evaluate(
            [ScenarioSource("falco", expected=True)],
            {},  # 完全收不到 heartbeat
        )
        falco = registry.get("falco")
        assert falco is not None, "掉線的來源不可從 registry 消失"
        assert falco.state is SourceState.STALE
        assert falco.last_heartbeat is None

    def test_never_reported_expected_source_is_stale_not_absent(self):
        """expected 但從未回報 heartbeat＝逾時的極端情形 → stale。"""
        registry = evaluate([ScenarioSource("loki", expected=True)], {})
        assert registry.get("loki").state is SourceState.STALE

    def test_heartbeat_for_source_not_in_scenario_is_ignored(self):
        """輸出清單只來自 scenario 定義。heartbeat 集合裡的多餘來源不得
        憑空長出一筆 —— 否則就等於用部署狀態驅動清單。"""
        registry = evaluate(
            [ScenarioSource("falco", expected=True)],
            {"falco": beats_ago(1), "phantom": beats_ago(1)},
        )
        assert {s.id for s in registry.sources} == {"falco"}
        assert registry.get("phantom") is None

    def test_not_expected_is_absent_even_with_fresh_heartbeat(self):
        """not expected 是第一判準：即便還在送 heartbeat，不在範圍就是 absent。"""
        registry = evaluate(
            [ScenarioSource("waf", expected=False)],
            {"waf": beats_ago(1)},
        )
        assert registry.get("waf").state is SourceState.ABSENT

    def test_duplicate_source_in_scenario_is_rejected(self):
        with pytest.raises(ValueError, match="重複"):
            evaluate(
                [ScenarioSource("falco"), ScenarioSource("falco")],
                {},
            )


class TestToleranceBoundary:
    """驗收條件：30s 間隔 / 90s 容忍，值為具名常數，邊界行為明寫。"""

    def test_named_constants_are_30_and_90(self):
        assert HEARTBEAT_INTERVAL_S == 30
        assert HEARTBEAT_TOLERANCE_S == 90

    def test_exactly_at_tolerance_is_healthy(self):
        """恰好等於容忍窗算 healthy —— 邊界含上緣，與 skew.py 的 <= 一致。"""
        registry = evaluate(
            [ScenarioSource("loki")],
            {"loki": beats_ago(HEARTBEAT_TOLERANCE_S)},
        )
        assert registry.get("loki").state is SourceState.HEALTHY

    def test_one_second_past_tolerance_is_stale(self):
        registry = evaluate(
            [ScenarioSource("loki")],
            {"loki": beats_ago(HEARTBEAT_TOLERANCE_S + 1)},
        )
        assert registry.get("loki").state is SourceState.STALE

    def test_caller_may_tighten_the_tolerance(self):
        heartbeats = {"loki": beats_ago(45)}
        assert evaluate([ScenarioSource("loki")], heartbeats).get("loki").state is (
            SourceState.HEALTHY
        )
        tight = evaluate([ScenarioSource("loki")], heartbeats, tolerance_s=30)
        assert tight.get("loki").state is SourceState.STALE
        assert tight.tolerance_s == 30


class TestRejectsNaiveDatetime:
    """無時區時間戳是整個 codebase 要消滅的東西（見 skew.py）。"""

    def test_naive_now_is_rejected(self):
        with pytest.raises(ValueError, match="timezone"):
            evaluate([ScenarioSource("loki")], {}, now=datetime(2026, 8, 8, 15, 0, 0))

    def test_naive_heartbeat_is_rejected(self):
        with pytest.raises(ValueError, match="timezone"):
            evaluate(
                [ScenarioSource("loki")],
                {"loki": datetime(2026, 8, 8, 14, 59, 0)},
            )


class TestStaleIntervalForUnknown:
    """spec §3.4：P2 用 stale 區間 ∩ 動作時間窗 判 unknown 並自動填原因。"""

    def test_healthy_source_has_no_stale_interval(self):
        registry = evaluate([ScenarioSource("loki")], {"loki": beats_ago(5)})
        assert registry.stale_interval("loki") is None

    def test_absent_source_has_no_stale_interval(self):
        registry = evaluate([ScenarioSource("waf", expected=False)], {})
        assert registry.stale_interval("waf") is None

    def test_stale_interval_runs_from_last_heartbeat_to_now(self):
        last = beats_ago(HEARTBEAT_TOLERANCE_S + 120)
        registry = evaluate([ScenarioSource("falco")], {"falco": last})
        interval = registry.stale_interval("falco")
        assert isinstance(interval, StaleInterval)
        assert interval.start == last
        assert interval.end == NOW

    def test_action_window_inside_gap_intersects(self):
        last = beats_ago(HEARTBEAT_TOLERANCE_S + 300)
        registry = evaluate([ScenarioSource("falco")], {"falco": last})
        interval = registry.stale_interval("falco")
        # 動作發生在 last 之後、now 之前 → 落在缺口內 → 相交 → 標 unknown
        action_start = last + timedelta(seconds=30)
        action_end = last + timedelta(seconds=60)
        assert interval.intersects(action_start, action_end)

    def test_action_window_entirely_before_last_heartbeat_does_not_intersect(self):
        last = beats_ago(HEARTBEAT_TOLERANCE_S + 300)
        registry = evaluate([ScenarioSource("falco")], {"falco": last})
        interval = registry.stale_interval("falco")
        # 動作在最後一次 heartbeat 之前 → 當時仍有覆蓋 → 不相交 → 不標 unknown
        action_start = last - timedelta(seconds=120)
        action_end = last - timedelta(seconds=60)
        assert not interval.intersects(action_start, action_end)

    def test_never_reported_source_gap_has_no_lower_bound(self):
        """從未回報的來源：區間左端無下界，任何 now 之前的動作都相交。"""
        registry = evaluate([ScenarioSource("falco")], {})
        interval = registry.stale_interval("falco")
        assert interval.start is None
        old = NOW - timedelta(hours=5)
        assert interval.intersects(old, old + timedelta(seconds=30))

    def test_reason_names_source_and_gap(self):
        last = datetime(2026, 8, 8, 14, 52, 3, tzinfo=timezone.utc)
        registry = evaluate([ScenarioSource("falco")], {"falco": last})
        reason = registry.stale_interval("falco").reason()
        assert "falco" in reason
        assert "無心跳" in reason
        assert last.isoformat() in reason

    def test_reason_for_never_reported_source(self):
        registry = evaluate([ScenarioSource("falco")], {})
        reason = registry.stale_interval("falco").reason()
        assert "falco" in reason
        assert "從未回報" in reason

    def test_stale_intervals_collects_every_stale_source(self):
        registry = evaluate(
            [
                ScenarioSource("falco"),
                ScenarioSource("loki"),
                ScenarioSource("waf", expected=False),
            ],
            {"falco": beats_ago(HEARTBEAT_TOLERANCE_S + 10), "loki": beats_ago(2)},
        )
        stale_sources = {i.source for i in registry.stale_intervals()}
        assert stale_sources == {"falco"}  # loki healthy、waf absent 都不列

    def test_intersects_rejects_reversed_window(self):
        registry = evaluate([ScenarioSource("falco")], {})
        interval = registry.stale_interval("falco")
        later = NOW
        earlier = NOW - timedelta(seconds=60)
        with pytest.raises(ValueError, match="顛倒"):
            interval.intersects(later, earlier)


class TestSnapshotShape:
    """spec §3.3：形狀欄位恰為 id / expected / last_heartbeat / state。"""

    def test_registry_dict_matches_spec_3_3(self):
        registry = evaluate(
            [
                ScenarioSource("application-log", expected=True),
                ScenarioSource("falco", expected=True),
                ScenarioSource("loki", expected=True),
                ScenarioSource("waf", expected=False),
            ],
            {
                "application-log": beats_ago(2),
                "falco": beats_ago(HEARTBEAT_TOLERANCE_S + 400),
                "loki": beats_ago(1),
            },
        )
        snapshot = registry.as_dict()
        assert snapshot["scenario_id"] == "sqli-01"
        assert [s["state"] for s in snapshot["sources"]] == [
            "healthy",
            "stale",
            "healthy",
            "absent",
        ]
        for source in snapshot["sources"]:
            assert set(source.keys()) == {"id", "expected", "last_heartbeat", "state"}
        # absent 來源的 last_heartbeat 為 null（spec §3.3 的 waf）
        waf = next(s for s in snapshot["sources"] if s["id"] == "waf")
        assert waf["last_heartbeat"] is None

    def test_last_heartbeat_is_iso8601_string(self):
        registry = evaluate([ScenarioSource("loki")], {"loki": NOW})
        loki = registry.as_dict()["sources"][0]
        assert loki["last_heartbeat"] == NOW.isoformat()
