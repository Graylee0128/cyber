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
3. `test_uncovered_action_is_detection_gap_not_visibility_gap` —— 決定性測試的真環境版：
   `detected` / `source_state` / `telemetry_present` 三個輸入**全部實採**，沒有字面值
4. `test_response_pull_blocks_only_attack_source_and_reset_clears` —— 票 #17：
   agent 主動 pull、以 Core Event `source_ip` 封鎖、回報 `response.executed`，Reset 復原

前提（由 scripts/test.sh 準備）：`range-up.sh --with-red --with-falco` 已跑完、
compose 全棧在跑。以 `PURPLE_RANGE_CHAIN=1` 開啟，否則 skip（一般 CI 無巢狀虛擬化）。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

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
REPO = Path(__file__).resolve().parents[2]


def _step(msg: str) -> None:
    print(f"    ▶ {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"    ✅ {msg}", flush=True)


def _curl_from_red(path: str, red: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", red, "curl", "-s", "-m", "5", f"http://{TARGET_IP}{path}"],
        capture_output=True, text=True, timeout=30,
    )


def _attack(path: str, red: str = "range-red1") -> str:
    """從紅隊容器打靶機 VM 的某個端點（隔著真 VLAN30→VLAN20）。"""
    result = _curl_from_red(path, red)
    assert result.returncode == 0, (
        f"{red} 打不到靶機 {TARGET_IP}{path}（§12.3 應允許 RED→TARGET:80）："
        f"{result.stdout} {result.stderr}"
    )
    return result.stdout.strip()


def _wait_red_reachable(red: str, timeout_s: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if _curl_from_red("/", red).returncode == 0:
            return True
        time.sleep(3)
    return False


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


def test_uncovered_action_is_detection_gap_not_visibility_gap(events):
    """決定性測試（真環境版）：`classify_miss` 的**三個輸入全部實採**，沒有字面值。

    #9 的 spec 是「當那條 Grafana rule 被停用時，呈現的是偵測缺口而不是可見性缺口」。
    改用**等價且可重複**的做法：靶機 `/uncovered` 會觸發一條 Falco 規則
    （`PurpleScope Uncovered Action`），而 `deploy/grafana` 刻意沒有對應的告警規則。
    於是 —— 遙測在、沒有告警 —— 正是「規則被停用」要製造的那個情形，
    但不必在測試裡去改 Grafana 的 provisioned 設定（那既不可重複、也會污染其他測試）。

    2026-08-09 code review 之前，這條測試把 `detected=False` 與
    `source_state=HEALTHY` 寫成字面值，只有 `telemetry_present` 是真的 —— 它證明的
    其實只是「Loki 裡有遙測」，卻掛著決定性測試的名字。現在三個輸入分別來自：
      detected          ← 真的去 Core Event 儲存查，確認**沒有**該情境的事件
      source_state      ← Falco 的事件正在進 Loki，即 collector 活著
      telemetry_present ← 真 Loki 查詢

    這條紅了代表實質退回 D3，Falco 覆蓋範圍內的偵測缺口全部不可觀測（ADR ③）。
    """
    print("\n=== [決定性/真環境] 有 Falco telemetry 但無規則覆蓋 → 偵測缺口 ===", flush=True)

    _step("前提①：確認 Grafana 真的沒有任何規則覆蓋 uncovered（設定層，不是靠註解宣稱）")
    rules_yaml = (
        Path(__file__).resolve().parents[2]
        / "deploy" / "grafana" / "provisioning" / "alerting" / "rules.yaml"
    ).read_text(encoding="utf-8")
    assert "uncovered" not in rules_yaml.lower(), (
        "有人幫 uncovered 加了 Grafana 規則 —— 這條測試的前提沒了。"
        "那條規則的價值就在於它**不被覆蓋**；要加規則請另立情境，別動這條。"
    )
    _ok("Grafana provisioned 規則中查無 uncovered —— 「沒有規則覆蓋」成立")

    mark = events.now()
    _step("從 range-red3 打靶機 /uncovered 三次（Falco 抓得到，但沒有規則會告警）")
    for _ in range(3):
        _attack("/uncovered", red="range-red3")

    _step("實採②：telemetry_present ← 真 Loki")
    uncovered_lines = _wait_loki("PurpleScope uncovered action")
    telemetry_present = uncovered_lines > 0
    assert telemetry_present, (
        "前提不滿足：Loki 查不到 uncovered 的 Falco 行。"
        "遙測都沒進來的話，判成可見性缺口才是對的，這條測試無從證明 D1。"
    )
    _ok(f"真 Loki 查到 {uncovered_lines} 行 → telemetry_present=True")

    _step("實採③：source_state ← Falco 是否還在產出事件（活著才算 HEALTHY）")
    falco_alive = loki_line_count(LOKI_URL, FALCO_SELECTOR, since_s=600) > 0
    source_state = SourceState.HEALTHY if falco_alive else SourceState.STALE
    assert source_state is SourceState.HEALTHY, (
        "Falco 沒在產出事件 —— 這是可見性缺口的情形，不是本測試要證明的偵測缺口"
    )
    _ok(f"Falco 事件持續進 Loki → source_state={source_state}")

    _step("實採①：detected ← 等過 Grafana 評估週期，確認真的沒有對應的 Core Event")
    time.sleep(45)  # Grafana eval 10s + webhook + receiver，留足餘裕
    new_events = events.since(mark)
    uncovered_events = [
        e for e in new_events
        if "uncovered" in str(e.get("scenario_id", "")).lower()
        or "uncovered" in str(e.get("technique", "")).lower()
    ]
    detected = bool(uncovered_events)
    assert not detected, f"不該有 uncovered 的 Core Event，卻查到：{uncovered_events}"
    _ok(f"45s 內 {len(new_events)} 筆新事件中，無一屬於 uncovered → detected=False")

    result = classify_miss(
        detected=detected,                    # ← 真的查過 Core Event 儲存
        source_state=source_state,            # ← 真的看過 Falco 還活著
        telemetry_present=telemetry_present,  # ← 真 Loki 查詢結果
    )
    assert result is MissClass.DETECTION_GAP
    assert result is not MissClass.VISIBILITY_GAP
    _ok("判定 DETECTION_GAP（看得到卻沒偵測到）—— D1 在真環境成立，三個輸入全部實採")


def test_response_pull_blocks_only_attack_source_and_reset_clears(events):
    """#17 真環境鏈：來源 IP 驅動封鎖，MTTR 終於 response.executed，Reset 復原。"""
    attacker = "range-red4"
    unaffected = "range-red5"
    attacker_ip = "10.167.30.14"
    print(
        "\n=== [#17 真環境全鏈] Falco → Core Event → agent pull → ipset → Reset ===",
        flush=True,
    )
    mark = events.now()

    _step("前置檢查：range-red4 與 range-red5 都能連靶機 :80（失敗即 fail，不 skip）")
    assert _wait_red_reachable(attacker), "range-red4 在前置檢查期間始終打不到靶機 :80"
    assert _wait_red_reachable(unaffected), "range-red5 在前置檢查期間始終打不到靶機 :80"

    try:
        _step("range-red4 觸發 /exec 五次；marker 由 TCP peer 帶出 source_ip")
        for _ in range(5):
            _attack("/exec", red=attacker)

        attack = wait_for_event(
            fetch=lambda: events.since(mark),
            match=lambda e: (
                e["scenario_id"] == "falco-exec-01"
                and e["event_type"] == "attack.detected"
                and e.get("target", {}).get("source_ip") == attacker_ip
            ),
            what="#17 含攻擊來源 IP 的 attack.detected Core Event",
            timeout_s=CHAIN_TIMEOUT_S,
            poll_s=3.0,
        )
        assert_core_event(attack)
        assert attack["target"]["source_ip"] == attacker_ip
        _ok(f"attack Core Event {attack['event_id']} 保留 source_ip={attacker_ip}")

        response = wait_for_event(
            fetch=lambda: events.since(mark),
            match=lambda e: (
                e["event_type"] == "response.executed"
                and e.get("target", {}).get("attack_event_id") == attack["event_id"]
            ),
            what="#17 agent 寫入 ipset 後的 response.executed Core Event",
            timeout_s=CHAIN_TIMEOUT_S,
            poll_s=3.0,
        )
        assert_core_event(response)
        assert response["target"]["source_ip"] == attacker_ip
        assert response["observed_at"] >= attack["observed_at"]
        _ok(f"response Core Event {response['event_id']}：MTTR 終點為 ipset 成功時刻")

        _step("確認只封鎖攻擊來源：red4 打 :80 失敗，red5 仍成功")
        blocked = _curl_from_red("/", attacker)
        assert blocked.returncode != 0, "range-red4 在 response.executed 後仍可連 :80"
        assert _curl_from_red("/", unaffected).returncode == 0, "封鎖誤傷 range-red5"
        _ok("精準封鎖成立，未影響其他 red 容器")
    finally:
        _step("執行正式 Reset 流程，避免封鎖狀態污染後續演練")
        subprocess.run(
            ["bash", str(REPO / "scripts" / "range" / "range-reset.sh"), "--with-red", "--with-falco"],
            check=True,
            cwd=REPO,
            timeout=900,
        )

    _step("Reset 後重新實採 red4 與 red5 對 :80 的連通性")
    assert _wait_red_reachable(attacker), "Reset 後 red4 封鎖狀態未清除"
    assert _wait_red_reachable(unaffected), "Reset 後 red5 未恢復連通"
    _ok("Reset 已清除 ipset/iptables 狀態，兩個來源均恢復連通")
