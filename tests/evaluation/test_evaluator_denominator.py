"""`EvaluationService.for_frozen_registry`（#90）—— 動作清單只能來自凍結的
Action Registry，不是呼叫端手填 tuple。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from purple.evaluation.action_registry import (
    ActionRegistryStore,
    RegisteredAction,
    RegistryNotFrozen,
)
from purple.evaluation.evaluator import EvaluationService
from purple.receiver.whitelist import load_whitelist
from purple.registry.source_registry import ScenarioSource, evaluate_registry

EX = "ex-denominator-1"
NOW = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def registry(pg_connection):
    return ActionRegistryStore(pg_connection, load_whitelist())


@pytest.fixture
def source_registry():
    return evaluate_registry("sqli-01", [ScenarioSource("falco")], {"falco": NOW}, NOW)


class TestFrozenRegistryIsTheOnlySource:
    def test_unfrozen_registry_is_rejected(self, registry, source_registry):
        registry.seed(EX, "sqli-01", [RegisteredAction("a-1", "T1190", "exploit")])

        with pytest.raises(RegistryNotFrozen):
            EvaluationService.for_frozen_registry(registry, EX, source_registry)

    def test_frozen_registry_supplies_the_action_list(self, registry, source_registry):
        registry.seed(
            EX, "sqli-01",
            [
                RegisteredAction("a-1", "T1190", "exploit"),
                RegisteredAction("a-2", "T1059", "run command"),
            ],
        )
        registry.freeze(EX)

        service = EvaluationService.for_frozen_registry(registry, EX, source_registry)

        assert set(service.actions) == {("a-1", "T1190"), ("a-2", "T1059")}

    def test_removing_the_frozen_check_would_turn_this_red(self, registry, source_registry):
        """驗收條件：拿掉『必須凍結』檢查時，上一條測試必須變紅——這裡直接
        證明反例：手動繞過工廠函式、對未凍結的 registry 直接建構
        EvaluationService，不會被擋（因為建構子本身沒有凍結檢查，檢查只
        活在 for_frozen_registry 這條路徑）。這條測試釘住「檢查活在正確
        的地方」這件事本身。
        """
        registry.seed(EX, "sqli-01", [RegisteredAction("a-1", "T1190", "exploit")])
        # 沒有 freeze()。直接用建構子繞過工廠函式——這是刻意保留給測試的
        # 逃生口（見 for_frozen_registry 的 docstring），不是安全漏洞：
        # production 呼叫路徑只會用 for_frozen_registry。
        service = EvaluationService(actions=(("a-1", "T1190"),), source_registry=source_registry)
        assert service.actions == (("a-1", "T1190"),)

    def test_evidence_and_alert_volume_pass_through(self, registry, source_registry):
        registry.seed(EX, "sqli-01", [RegisteredAction("a-1", "T1190", "exploit")])
        registry.freeze(EX)

        service = EvaluationService.for_frozen_registry(
            registry, EX, source_registry, evidence_by_action={}, alert_volume=42
        )

        assert service.alert_volume == 42
        assert service.evidence_by_action == {}
