"""票 #9 真環境驗收 —— Falco 作為 runtime sensor（需 docker compose 全棧）。

契約測試（tests/pipeline/test_falco_produces_core_event）手餵 webhook；這裡打真的
`/exec` 讓它自己走完：
    vulnerable-app /exec → 生 shell 直譯器 → Falco(modern-eBPF) 抓 execve
    → Alloy → Loki → Grafana（唯一 alert engine）→ webhook → receiver → Core Event(T1059)

兩條測試：
1. `test_falco_exec_becomes_core_event_T1059` —— 完整管線通。
2. `test_falco_disabled_rule_is_detection_gap_not_visibility_gap` —— 決定性測試的
   **真環境版**：`telemetry_present` 不再是字面 True，而是**真的去查 Loki**。規則沒
   開火（detected=False）但 Loki 裡確實有 Falco 那筆 → 判 DETECTION_GAP。這條紅了
   代表我們實質退回 D3（ADR ③），Falco 覆蓋範圍內的偵測缺口全部不可觀測。

Falco 依賴 modern-eBPF / 主機 BTF；若 CI runner 不支援，Falco 起不來、這兩條會失敗
（而非誤綠）。真環境（大主機，2b-① 已證此 kernel 支援）必綠。
"""

from __future__ import annotations

import os
import time

import pytest

from purple.harness import assert_core_event, loki_line_count, trigger_exec, wait_for_event
from purple.metrics.gaps import MissClass, classify_miss
from purple.registry.source_registry import SourceState
from purple.store.events import CoreEventStore

pytestmark = pytest.mark.integration

APP_URL = os.environ.get("PURPLE_APP_URL", "http://localhost:8080")
LOKI_URL = os.environ.get("PURPLE_LOKI_URL", "http://localhost:3100")

E2E_TIMEOUT_S = 90.0
FALCO_SELECTOR = '{job="falco"}'
FALCO_NEEDLE = "PurpleScope exec detected"


def _step(msg: str) -> None:
    print(f"    ▶ {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"    ✅ {msg}", flush=True)


@pytest.fixture
def events(pg_connection):
    return CoreEventStore(pg_connection)


def test_falco_exec_becomes_core_event_T1059(events):
    """happy path：真 /exec → Falco 抓 execve → 走完管線 → attack.detected(T1059)。"""
    print("\n=== [happy path] 真 exec → Falco → Core Event(T1059) ===", flush=True)
    mark = events.now()
    _step("打 5 次 /exec（在靶機內生帶標記的 shell）")
    for _ in range(5):
        trigger_exec(APP_URL)

    _step("等管線：Falco → Alloy → Loki → Grafana(eval 10s) → webhook → receiver")
    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "falco-exec-01" and e["event_type"] == "attack.detected",
        what="真實 exec 走完管線後的 attack.detected Core Event",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    assert_core_event(core)
    assert core["technique"] == "T1059"
    _ok("technique = T1059（Falco syscall 路徑）")
    assert core["visibility"] == "public"
    assert "evidence_ref" not in core and "loki" not in repr(core).lower()
    _ok("符合 Core Event 契約、無 backend 洩漏")


def test_falco_disabled_rule_is_detection_gap_not_visibility_gap(events):
    """決定性測試（真環境版）：telemetry_present 來自**真 Loki 查詢**，非字面值。

    先打 /exec，確認 Falco 那筆真的進了 Loki（telemetry_present 由查詢決定）；
    在「規則未開火」的前提下，分類必須是 DETECTION_GAP，不是 VISIBILITY_GAP。
    """
    print("\n=== [決定性/真環境] 有 Falco telemetry 但沒告警 → 偵測缺口 ===", flush=True)
    _step("打 5 次 /exec，讓 Falco 事件確實進 Loki")
    for _ in range(5):
        trigger_exec(APP_URL)

    _step(f"查真 Loki：{FALCO_SELECTOR} |= `{FALCO_NEEDLE}` 是否有行")
    deadline = time.monotonic() + E2E_TIMEOUT_S
    count = 0
    while time.monotonic() < deadline:
        count = loki_line_count(LOKI_URL, FALCO_SELECTOR, FALCO_NEEDLE, since_s=300)
        if count > 0:
            break
        time.sleep(2.0)
    assert count > 0, (
        f"等了 {E2E_TIMEOUT_S:g}s，Loki 仍查不到 Falco exec 遙測 "
        f"（{FALCO_SELECTOR} |= `{FALCO_NEEDLE}`）—— Falco→Alloy→Loki 這段沒通，"
        f"telemetry_present 無法成立，決定性測試前提不滿足"
    )
    telemetry_present = count > 0
    _ok(f"Loki 有 {count} 行 Falco 遙測 → telemetry_present={telemetry_present}（真查詢）")

    # 關鍵：telemetry_present 是真 Loki 查來的。規則沒開火時，這必須是偵測缺口。
    result = classify_miss(
        detected=False,                       # 假設這條 Grafana rule 被停用 → 沒告警
        source_state=SourceState.HEALTHY,     # Falco 活著（能寫進 Loki 即證）
        telemetry_present=telemetry_present,   # ← 來自真 Loki，不是字面 True
    )
    assert result is MissClass.DETECTION_GAP
    assert result is not MissClass.VISIBILITY_GAP
    _ok("判定 DETECTION_GAP（看得到卻沒偵測到）—— D1 在真環境成立")
