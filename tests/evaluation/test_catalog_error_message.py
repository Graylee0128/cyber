"""#143 item 6：未登記的 scenario 要回一句做得下去的話。

這條路徑在真部署裡最常被踩到 —— 測試用的 exercise 掛的 scenario（例如
`admission-e2e`）沒在 `config/scenario-sources.yaml` 登記，Purple Console 的
涵蓋率表與 Battleboard 的攻防進度就整個空掉。狀態碼本來就對（503，不是 500），
問題在訊息：`未知 scenario 'admission-e2e'` 講不出要動哪個檔，而前端對 503
的預設說法是「後端服務尚未就緒」，把改一行 YAML 講成後端掛了。

`EvaluationAssembler.build()` 在碰 `source_registry()`（本測試要炸的那一步）之前，
會先真的查一次 `ActionRegistryStore.get()`／`frozen_actions()`（分母的唯一入口，
`assembly.py:64-68` 的順序）——所以這裡必須先用 `pg_connection` seed＋freeze 一份
真的 registry，不能只塞一個只有 `close()` 的假連線；否則測試在真正炸到
`CatalogError` 之前就先死在 `AttributeError: '_Conn' object has no attribute
'execute'`（2026-08-15 CI 撞過一次，960 過 1 敗）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from purple.evaluation.action_registry import ActionRegistryStore, RegisteredAction
from purple.evaluation.api import CATALOG_HINT, create_app
from purple.receiver.whitelist import default_whitelist
from purple.registry.production import CatalogError
from purple.registry.source_registry import evaluate_registry


def _seed_and_freeze(pg_connection, exercise_id: str, scenario_id: str) -> None:
    registry = ActionRegistryStore(pg_connection, default_whitelist())
    registry.seed(exercise_id, scenario_id, [RegisteredAction("a-1", "T1190", "exploit public app")])
    registry.freeze(exercise_id)


def _client(*, source_registry):
    # `connection_factory` 刻意留預設（生產路徑的 `connect`，讀同一個
    # `PURPLE_PG_DSN`）：每個請求開自己的連線、自己關，不會動到 `pg_connection`
    # 那條測試套件共用、跨測試存活的連線。
    return TestClient(create_app(source_registry=source_registry, telemetry=object()))


def test_unregistered_scenario_names_the_file_and_block(pg_connection):
    exercise_id = "ex-catalog-hint"
    _seed_and_freeze(pg_connection, exercise_id, "admission-e2e")

    def refuse(scenario_id: str):
        raise CatalogError(f"未知 scenario {scenario_id!r}")

    response = _client(source_registry=refuse).get(f"/api/exercises/{exercise_id}/evaluation")

    assert response.status_code == 503
    detail = response.json()["detail"]
    # 原始判定保留（是「未知」而不是別的失敗），但後面要接得上下一步。
    assert "未知 scenario" in detail
    assert CATALOG_HINT in detail
    assert "config/scenario-sources.yaml" in detail


def test_registered_scenario_does_not_trigger_the_hint(pg_connection):
    """對照組：已登記的 scenario 不該平白多出這段 hint 文字。

    這場 exercise 只 seed 了 registry、沒有任何 action execution，所以
    `EvaluationAssembler.build()` 的證據迴圈整個跳過——不需要真的 telemetry
    backend 就能走完，`object()` 這個假 backend 不會被碰到。
    """
    exercise_id = "ex-catalog-hint-registered"
    _seed_and_freeze(pg_connection, exercise_id, "shopdb-credential-pivot")
    now = datetime.now(timezone.utc)

    response = _client(
        source_registry=lambda scenario_id: evaluate_registry(scenario_id, [], {}, now)
    ).get(f"/api/exercises/{exercise_id}/evaluation")

    assert response.status_code == 200
    assert CATALOG_HINT not in response.text


def test_the_hint_says_which_block_to_edit():
    """檔名對了但區塊名沒講，讀的人仍得自己翻 —— 那份 YAML 有 sources／
    scenarios／fixtures 三個區塊，登記錯區塊查不到還是同一個 503。"""
    assert "scenarios:" in CATALOG_HINT
    assert "expected_sources" in CATALOG_HINT
