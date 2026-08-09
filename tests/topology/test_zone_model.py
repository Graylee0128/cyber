"""票 #15 —— compose 四區網段模型（純判定，真 TDD，不需 docker）。

`check_zone_assignments` 對「service → 它所在的 compose networks」這份對映，
驗證 **Docker network membership 能強制**的區規則（SA §12）：

- 每個 mgmt 平面住戶都在 z-mgmt
- collector（Alloy）在 target 側（契約 4）
- 沒有任何容器同時橋接 z-red 與 z-mgmt（否則契約 3 RED→MGMT 結構上就破了）

**不**檢查的（Docker membership 做不到，委派 #13）：契約 2 的方向性、
真 VLAN/macvlan、六台 kali 可分辨 source IP、真單向防火牆。

最後一條測試直接讀真的 docker-compose.yml，擋 compose 漂移 —— 不需 docker，
只 parse YAML。
"""

from pathlib import Path

import yaml

from purple.topology_check import (
    Z_MGMT,
    Z_RED,
    Z_TARGET,
    check_zone_assignments,
)

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def _good_map() -> dict[str, set[str]]:
    return {
        "postgres": {Z_MGMT},
        "loki": {Z_MGMT},
        "grafana": {Z_MGMT},
        "receiver": {Z_MGMT},
        "evaluation-engine": {Z_MGMT},
        "prometheus": {Z_MGMT, Z_TARGET},
        "vulnerable-app": {Z_TARGET},
        "alloy": {Z_TARGET, Z_MGMT},
    }


def test_compliant_map_has_no_violations():
    assert check_zone_assignments(_good_map()) == []


def test_mgmt_resident_off_mgmt_is_flagged():
    bad = _good_map()
    bad["postgres"] = {Z_TARGET}  # postgres 掉到 target 側
    fails = check_zone_assignments(bad)
    assert any("postgres" in f and Z_MGMT in f for f in fails)


def test_collector_not_on_target_breaks_contract_4():
    bad = _good_map()
    bad["alloy"] = {Z_MGMT}  # collector 跑到 mgmt 側 —— 契約 4 破
    fails = check_zone_assignments(bad)
    assert any("契約4" in f and "alloy" in f for f in fails)


def test_container_bridging_red_and_mgmt_breaks_contract_3():
    bad = _good_map()
    bad["rogue"] = {Z_RED, Z_MGMT}  # 一個容器同時連 red 與 mgmt
    fails = check_zone_assignments(bad)
    assert any("契約3" in f and "rogue" in f for f in fails)


def test_absent_service_is_skipped_not_failed():
    """compose 子集（如只起 postgres）不該因缺服務就報違規。"""
    assert check_zone_assignments({"postgres": {Z_MGMT}}) == []


def test_real_compose_file_is_zone_compliant():
    """擋 compose 漂移：真的 docker-compose.yml 必須符合可強制的區規則。"""
    cfg = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service_networks = {
        name: set((svc.get("networks") or {}).keys() if isinstance(svc.get("networks"), dict)
                  else (svc.get("networks") or []))
        for name, svc in cfg["services"].items()
    }
    assert check_zone_assignments(service_networks) == []


def test_real_compose_defines_all_four_zones():
    cfg = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert {"z-app", "z-mgmt", "z-target", "z-red"} <= set(cfg.get("networks", {}))
