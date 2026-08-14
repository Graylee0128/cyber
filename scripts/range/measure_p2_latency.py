#!/usr/bin/env python3
"""#90 Phase 4 —— 對靶機 VM 的可計分 SQLi 攻擊面實跑 20 次 detection latency 量測並持久化。

這是「實際跑滿 20 次」那條驗收（被 #44 阻擋、現已解除）的操作載具。與純函數層
（`summarize_latency` / `LatencyAssembler` / `LatencySummaryStore`，#21/#91 已交付且測完）
不同：這支吃真管線的事件，把 p50/p95 寫進真 Postgres。

流程（全部走真相來源，沒有 fixture 餵值）：

  1. 起一場 p2-latency-baseline 演練（20 個 T1190 SQLi 註冊動作），凍結 registry。
  2. 對每個 action 記一筆 execution（executed_at = 攻擊送出時刻），marker 帶 action_id。
  3. 從紅隊容器對靶機 VM `/product` 送 20 次 UNION SQLi，各自帶自己的 action marker。
  4. 等偵測層（SQLInjectionBurstTarget，by source_ip, action_id）把 20 筆 firing
     Core Event 落地，各自帶回自己的 action_id。
  5. `LatencyAssembler` 以 action_id 逐次關聯 → `summarize_latency` 算 p50/p95 →
     `LatencySummaryStore` 持久化。
  6. 另開一條連線讀回，證明重啟後查得到（持久化不是記憶體裡的假象）。

前提：range 已起（golden 靶機 + 紅隊容器）、compose 全棧含 Grafana 在跑。
以 graylee（docker 群組）即可執行，不需 root。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from purple.evaluation.action_registry import ActionRegistryStore, RegisteredAction  # noqa: E402
from purple.evaluation.latency import LatencyAssembler, summarize_latency  # noqa: E402
from purple.receiver.whitelist import default_whitelist  # noqa: E402
from purple.store.db import connect, ensure_schema as ensure_purple_schema  # noqa: E402
from purple.store.events import CoreEventStore  # noqa: E402
from purple.store.executions import ActionExecutionStore  # noqa: E402
from purple.store.latency import LatencySummaryStore  # noqa: E402
from purple.harness.attacker import make_marker  # noqa: E402
from range_core.exercises import (  # noqa: E402
    ExerciseStore,
    PlayerRegistration,
    ensure_schema as ensure_exercise_schema,
)
from range_core.scenarios import load_scenario  # noqa: E402

SCENARIO = (
    REPO / "tests" / "integration" / "fixtures" / "scenarios"
    / "p2-latency-baseline" / "metadata.yaml"
)
DEFAULT_TARGET_IP = "10.167.20.10"
DEFAULT_RED = "range-red1"
DEFAULT_SOURCE_IP = "10.167.30.11"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fire_sqli(red: str, target_ip: str, action_id: str) -> str:
    """從紅隊容器對靶機 /product 送一次 UNION SQLi，marker 帶 action_id。回傳 marker。"""
    attack_id = f"{action_id}-{int(time.time()*1000)}"
    marker = make_marker(attack_id, action_id)
    # UNION 撈 credentials（真攻擊面）＋ 尾綴 marker 的 SQL 註解。空白 URL-encode。
    raw_id = f"0 UNION SELECT id,service,username,password FROM credentials {marker}"
    query = raw_id.replace(" ", "%20")
    url = f"http://{target_ip}/product?id={query}"
    result = subprocess.run(
        ["docker", "exec", red, "curl", "-s", "-m", "5", url],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{red} 打不到靶機 {url}: {result.stderr}")
    return marker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-ip", default=DEFAULT_TARGET_IP)
    parser.add_argument("--red", default=DEFAULT_RED)
    parser.add_argument("--source-ip", default=DEFAULT_SOURCE_IP)
    parser.add_argument("--firing-timeout-s", type=float, default=180.0)
    args = parser.parse_args()

    scenario = load_scenario(SCENARIO)
    actions = scenario.action_registry_seed()
    assert len(actions) == 20, f"latency baseline 需要 20 個動作，實得 {len(actions)}"

    conn = connect()

    # compose 沒有跑 range_core admission 服務，這條 PG 因此可能沒有 exercises／
    # registered_actions 等表。量測前先確保 schema（IF NOT EXISTS，冪等）。
    ensure_purple_schema(conn)
    ensure_exercise_schema(conn)

    # 乾淨起點：結束任何還在跑的演練（partial unique index 只允許一場 running）。
    conn.execute("UPDATE exercises SET state='ended', ended_at=now() WHERE state='running'")

    exercises = ExerciseStore(conn)
    exercise = exercises.start(
        scenario,
        (PlayerRegistration(player_id="latency-red", source_ip=args.source_ip),),
    )
    ex = exercise.exercise_id
    print(f"▶ 演練 {ex} 起動（scenario={scenario.id}）", flush=True)

    registry = ActionRegistryStore(conn, default_whitelist())
    registry.seed(ex, scenario.id, [RegisteredAction(a.id, a.technique, a.description) for a in actions])
    registry.freeze(ex)
    print(f"▶ registry 凍結：{len(actions)} 個動作", flush=True)

    executions = ActionExecutionStore(conn)
    print("▶ 送 20 次 SQLi（各自帶 action marker），逐次記 execution", flush=True)
    for action in actions:
        executed_at = _now()
        marker = _fire_sqli(args.red, args.target_ip, action.id)
        executions.record(ex, action.id, executed_at, marker)

    events = CoreEventStore(conn)
    print("▶ 等偵測層把 20 筆 firing Core Event 落地（各自帶回 action_id）", flush=True)
    deadline = time.monotonic() + args.firing_timeout_s
    seen = 0
    while time.monotonic() < deadline:
        firings = events.firings_by_action(ex)
        seen = len(firings)
        if seen >= 20:
            break
        print(f"   … 已關聯 {seen}/20 firing", flush=True)
        time.sleep(5)
    firings = events.firings_by_action(ex)
    if len(firings) < 20:
        print(f"❌ 只等到 {len(firings)}/20 firing（action_id 關聯）。", flush=True)
        print("   查：Grafana 是否載入新 rule（SQLInjectionBurstTarget）、靶機 golden 是否為新版"
              "（app.py 記 sqli_suspected）、Alloy→Loki 是否通。", flush=True)
        return 1
    print(f"✓ 20 筆 firing 全部以 action_id 關聯", flush=True)

    runs = LatencyAssembler(
        executions=executions, events=events, mode_of=lambda _a: "exercise"
    ).build(ex)
    summaries = summarize_latency(runs)
    LatencySummaryStore(conn).save_all(ex, summaries)
    print("▶ p50/p95 已持久化", flush=True)

    # 另開連線讀回 —— 證明是 DB 落地，不是同一連線的記憶體殘影。
    verify_conn = connect()
    reread = LatencySummaryStore(verify_conn).for_exercise(ex)
    verify_conn.close()
    conn.close()

    assert reread, "重讀不到已存的 latency 摘要"
    for mode, s in reread.items():
        print(
            f"  [{mode}] samples={s.sample_count} "
            f"MTTD p50={s.mttd_p50_ms}ms p95={s.mttd_p95_ms}ms "
            f"MTTR p50={s.mttr_p50_ms}ms p95={s.mttr_p95_ms}ms",
            flush=True,
        )
    print(f"✅ #90 Phase 4：20 次真實量測完成並持久化（exercise={ex}）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
