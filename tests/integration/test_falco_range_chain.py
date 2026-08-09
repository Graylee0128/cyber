"""票 #9 真環境全鏈驗收 —— 紅隊隔著真 VLAN 打靶機 VM，事件走完管線變成 Core Event。

這是整個 P1 最後一段「真環境」證據，與 compose 版（test_falco_pipeline.py）的差別：
偵測來自**真 VM 內的 Falco(modern-eBPF)**，遙測經 target 側 Alloy 推過真 VLAN 到
Z-MGMT 的 Loki（TARGET→MGMT :3100＝契約 1 實用），紅隊是接 VLAN30 的真容器。

    range-red1(10.167.30.11) → 靶機 VM :80 (/exec, /readsecret)
      → Falco 抓 execve / open → Alloy → Loki(10.167.10.20:3100)
      → Grafana(唯一 alert engine) → webhook → receiver → Core Event

三條：
1. `test_exec_becomes_core_event_T1059` —— SA §7 執行面
2. `test_sensitive_file_access_becomes_core_event_T1005` —— SA §7 Scenario 03
3. `test_disabled_rule_is_detection_gap_not_visibility_gap` —— 決定性測試的真環境版，
   `telemetry_present` 來自真 Loki 查詢

前提（由 scripts/test.sh 準備）：`range-up.sh --with-red --with-falco` 已跑完、
compose 全棧在跑。以 `PURPLE_RANGE_CHAIN=1` 開啟，否則 skip（一般 CI 無巢狀虛擬化）。
"""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from purple.harness import assert_core_event, loki_line_count, wait_for_event
from purple.metrics.gaps import MissClass, classify_miss
from purple.registry.source_registry import SourceState
from purple.store.events import CoreEventStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PURPLE_RANGE_CHAIN") != "1",
        reason="需真 range（range-up --with-red --with-falco）+ PURPLE_RANGE_CHAIN=1",
    ),
]

LOKI_URL = os.environ.get("PURPLE_LOKI_URL", "http://localhost:3100")
TARGET_IP = os.environ.get("PURPLE_RANGE_TARGET_IP", "10.167.20.10")
CHAIN_TIMEOUT_S = 120.0
FALCO_SELECTOR = '{job="falco"}'


def _step(msg: str) -> None:
    print(f"    ▶ {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"    ✅ {msg}", flush=True)


def _attack(path: str, red: str = "range-red1") -> str:
    """從紅隊容器打靶機 VM 的某個端點（隔著真 VLAN30→VLAN20）。"""
    result = subprocess.run(
        ["docker", "exec", red, "curl", "-s", "-m", "5", f"http://{TARGET_IP}{path}"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"{red} 打不到靶機 {TARGET_IP}{path}（§12.3 應允許 RED→TARGET:80）："
        f"{result.stdout} {result.stderr}"
    )
    return result.stdout.strip()


def _wait_loki(needle: str, timeout_s: float = CHAIN_TIMEOUT_S) -> int:
    """等真 Loki 出現含 needle 的 Falco 行；回傳行數。"""
    deadline = time.monotonic() + timeout_s
    count = 0
    while time.monotonic() < deadline:
        count = loki_line_count(LOKI_URL, FALCO_SELECTOR, needle, since_s=600)
        if count > 0:
            return count
        time.sleep(3)
    return count


@pytest.fixture
def events(pg_connection):
    return CoreEventStore(pg_connection)


def test_exec_becomes_core_event_T1059(events):
    """紅隊打 /exec → 靶機生 shell → Falco 抓 execve → 全鏈 → Core Event(T1059)。"""
    print("\n=== [真環境全鏈] red → VM:80/exec → Falco → … → Core Event(T1059) ===", flush=True)
    mark = events.now()

    _step("從 range-red1（10.167.30.11）打靶機 /exec 五次")
    for _ in range(5):
        _attack("/exec")

    _step("等 Falco 事件經 Alloy 推到 Z-MGMT 的 Loki（契約 1 實用）")
    count = _wait_loki("PurpleScope exec detected")
    assert count > 0, "Loki 查不到 Falco 的 exec 事件 —— VM 內 Falco/Alloy 或契約 1 有問題"
    _ok(f"Loki 有 {count} 行 Falco exec 事件（真 VM → 真 VLAN → 真 Loki）")

    _step("等 Grafana(eval 10s) → webhook → receiver → Core Event")
    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "falco-exec-01" and e["event_type"] == "attack.detected",
        what="真 Falco 走完全鏈的 attack.detected Core Event",
        timeout_s=CHAIN_TIMEOUT_S,
        poll_s=3.0,
    )
    assert_core_event(core)
    assert core["technique"] == "T1059"
    assert core["visibility"] == "public"
    assert "evidence_ref" not in core and "loki" not in repr(core).lower()
    _ok(f"Core Event {core['event_id']} technique=T1059，契約完整、無 backend 洩漏")


def test_sensitive_file_access_becomes_core_event_T1005(events):
    """SA §7 Scenario 03：紅隊打 /readsecret → Falco 抓 open → Core Event(T1005)。"""
    print("\n=== [Scenario 03] 敏感檔存取 → Falco → … → Core Event(T1005) ===", flush=True)
    mark = events.now()

    _step("從 range-red2 打靶機 /readsecret 五次")
    for _ in range(5):
        _attack("/readsecret", red="range-red2")

    _step("等 Falco 敏感檔事件進 Loki")
    count = _wait_loki("PurpleScope sensitive file access")
    assert count > 0, "Loki 查不到 Falco 的敏感檔事件"
    _ok(f"Loki 有 {count} 行敏感檔存取事件")

    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "falco-secret-03" and e["event_type"] == "attack.detected",
        what="Scenario 03 的 attack.detected Core Event",
        timeout_s=CHAIN_TIMEOUT_S,
        poll_s=3.0,
    )
    assert_core_event(core)
    assert core["technique"] == "T1005"
    _ok(f"Core Event {core['event_id']} technique=T1005（SA §7 Scenario 03 通）")


def test_disabled_rule_is_detection_gap_not_visibility_gap():
    """決定性測試（真環境版）：telemetry_present 來自真 Loki，不是字面值。

    規則沒開火時，只要 raw 遙測在，就必須判偵測缺口 —— 這條紅了代表實質退回 D3，
    Falco 覆蓋範圍內的偵測缺口全部不可觀測（ADR ③）。
    """
    print("\n=== [決定性/真環境] 有 Falco telemetry 但沒告警 → 偵測缺口 ===", flush=True)
    _step("打 /exec 讓 Falco 事件確實進 Loki")
    for _ in range(3):
        _attack("/exec")

    count = _wait_loki("PurpleScope exec detected")
    telemetry_present = count > 0
    assert telemetry_present, "前提不滿足：Loki 沒有 Falco 遙測"
    _ok(f"真 Loki 查到 {count} 行 → telemetry_present={telemetry_present}")

    result = classify_miss(
        detected=False,                      # 假設該 Grafana rule 被停用 → 沒告警
        source_state=SourceState.HEALTHY,    # Falco 活著（事件進得了 Loki 即證）
        telemetry_present=telemetry_present,  # ← 真 Loki 查詢結果
    )
    assert result is MissClass.DETECTION_GAP
    assert result is not MissClass.VISIBILITY_GAP
    _ok("判定 DETECTION_GAP（看得到卻沒偵測到）—— D1 在真環境成立")
