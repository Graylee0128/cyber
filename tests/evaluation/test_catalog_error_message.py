"""#143 item 6：未登記的 scenario 要回一句做得下去的話。

這條路徑在真部署裡最常被踩到 —— 測試用的 exercise 掛的 scenario（例如
`admission-e2e`）沒在 `config/scenario-sources.yaml` 登記，Purple Console 的
涵蓋率表與 Battleboard 的攻防進度就整個空掉。狀態碼本來就對（503，不是 500），
問題在訊息：`未知 scenario 'admission-e2e'` 講不出要動哪個檔，而前端對 503
的預設說法是「後端服務尚未就緒」，把改一行 YAML 講成後端掛了。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from purple.evaluation.api import CATALOG_HINT, create_app
from purple.registry.production import CatalogError


class _Conn:
    """`_session` 只要求能 close；catalog 查詢在碰資料庫之前就炸了。"""

    def close(self) -> None: ...


@pytest.fixture
def unregistered_scenario_client(monkeypatch):
    def refuse(scenario_id: str):
        raise CatalogError(f"未知 scenario {scenario_id!r}")

    monkeypatch.setattr("purple.evaluation.api.ensure_schema", lambda conn: None)
    app = create_app(
        connection_factory=_Conn,
        source_registry=refuse,
        telemetry=object(),
    )
    return TestClient(app)


def test_unregistered_scenario_names_the_file_and_block(unregistered_scenario_client):
    response = unregistered_scenario_client.get("/api/exercises/ex-1/evaluation")

    assert response.status_code == 503
    detail = response.json()["detail"]
    # 原始判定保留（是「未知」而不是別的失敗），但後面要接得上下一步。
    assert "未知 scenario" in detail
    assert CATALOG_HINT in detail
    assert "config/scenario-sources.yaml" in detail


def test_the_hint_says_which_block_to_edit():
    """檔名對了但區塊名沒講，讀的人仍得自己翻 —— 那份 YAML 有 sources／
    scenarios／fixtures 三個區塊，登記錯區塊查不到還是同一個 503。"""
    assert "scenarios:" in CATALOG_HINT
    assert "expected_sources" in CATALOG_HINT
