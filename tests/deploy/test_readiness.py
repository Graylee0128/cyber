"""票 #16 —— Z-MGMT 住戶部署就緒判定（純函式，真 TDD，不需 docker）。

`unhealthy_residents` 給 `docker compose ps --format json` 的 rows，回傳
expected 住戶中「沒起來 / 沒 running / 沒 healthy」的清單。空清單＝整組就緒。

這條純判定讓 smoke test 能在**任一住戶未起或不健康時失敗**（驗收 4），
且判定邏輯本身可離線單元測試。
"""

from purple.topology_check import (
    MGMT_HEALTHCHECKED,
    MGMT_RESIDENTS,
    unhealthy_residents,
)


def _row(service: str, state: str = "running", health: str = "healthy") -> dict:
    return {"Service": service, "State": state, "Health": health}


def _all_healthy() -> list[dict]:
    return [_row(s) for s in MGMT_HEALTHCHECKED]


def test_all_running_and_healthy_is_empty():
    assert unhealthy_residents(_all_healthy(), expected=MGMT_HEALTHCHECKED) == []


def test_exited_resident_is_flagged():
    rows = _all_healthy()
    rows[0] = _row(rows[0]["Service"], state="exited")
    bad = unhealthy_residents(rows, expected=MGMT_HEALTHCHECKED)
    assert any(rows[0]["Service"] in b and "exited" in b for b in bad)


def test_resident_without_healthcheck_is_flagged():
    """Health 空字串＝該容器根本沒宣告 healthcheck → 不算就緒。"""
    rows = _all_healthy()
    victim = rows[0]["Service"]
    rows[0] = _row(victim, health="")
    bad = unhealthy_residents(rows, expected=MGMT_HEALTHCHECKED)
    assert any(victim in b and "healthcheck" in b for b in bad)


def test_absent_resident_is_flagged_as_not_up():
    """住戶完全沒出現在 ps → 沒起來，必須入列（不能靜默略過）。"""
    rows = [r for r in _all_healthy() if r["Service"] != "receiver"]
    bad = unhealthy_residents(rows, expected=MGMT_HEALTHCHECKED)
    assert any("receiver" in b for b in bad)


def test_non_resident_health_is_ignored():
    """非 expected 的服務（如 z-target 的 app）不健康，不影響 mgmt 就緒判定。"""
    rows = _all_healthy() + [_row("vulnerable-app", state="exited")]
    assert unhealthy_residents(rows, expected=MGMT_HEALTHCHECKED) == []


def test_loki_is_excluded_from_docker_healthchecked_set():
    """Loki 是 distroless，無法容器內 healthcheck，不納入 Docker gate。"""
    assert "loki" in MGMT_RESIDENTS
    assert "loki" not in MGMT_HEALTHCHECKED
