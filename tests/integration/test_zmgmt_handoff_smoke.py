"""票 #16 —— 「Z-MGMT 軟體完成」里程碑的可執行定義（需 docker compose 全棧）。

兩條 smoke，合起來就是里程碑：

1. **住戶健康 gate**：`docker compose ps` 裡每個 Z-MGMT 住戶都 running+healthy
   （loki distroless 無容器內 healthcheck，改由 host 探 :3100/ready 補上）。
   任一住戶沒起／不健康 → 失敗（驗收 4）。
2. **P1→P2 handoff**：真 SQLi → Core Event 落地（P1 receiver）→ 以該 event_id
   呼叫 Evidence API 取回上下文窗（P2 evaluation-engine），全程在 z-mgmt 內。
   handoff 任一環斷掉 → 失敗（驗收 3、4）。

真網段隔離（VLAN/firewall/deploy）由 #13 收尾——這裡是**軟體**完成，不是環境完成。
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from purple.harness import wait_for_event
from purple.harness.attacker import inject_sqli
from purple.store.events import CoreEventStore
from purple.topology_check import MGMT_HEALTHCHECKED, unhealthy_residents

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_URL = os.environ.get("PURPLE_APP_URL", "http://localhost:8080")
ENGINE_URL = os.environ.get("PURPLE_ENGINE_URL", "http://localhost:8001")
LOKI_URL = os.environ.get("PURPLE_LOKI_URL", "http://localhost:3100")
# #52 B2：需與 docker-compose.yml 的 evaluation-engine 服務值一致。
EVIDENCE_TOKEN_BLUE = os.environ.get("PURPLE_EVIDENCE_TOKEN_BLUE", "dev-blue-token")
E2E_TIMEOUT_S = 90.0


def _step(msg: str) -> None:
    print(f"    ▶ {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"    ✅ {msg}", flush=True)


@pytest.fixture
def events(pg_connection):
    return CoreEventStore(pg_connection)


def _compose_ps() -> list[dict]:
    raw = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert raw.returncode == 0, raw.stderr
    # 新版 compose 是 NDJSON（一行一個 service）。
    return [json.loads(line) for line in raw.stdout.splitlines() if line.strip()]


def _loki_ready(timeout_s: float = 30.0) -> bool:
    """Loki 就緒＝能回應 LogQL 查詢 API。

    刻意不用 /ready：Loki single-binary 的 /ready 帶 ring 就緒延遲，會在「其實
    已能查詢」後仍回 503 一段時間，是比實際可用更嚴格且抖動的信號。改探查詢 API
    /loki/api/v1/labels（就緒後回 200），直接證明它真在服務查詢。加重試窗吸收啟動抖動。
    """
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{LOKI_URL}/loki/api/v1/labels", timeout=5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass
        time.sleep(2)
    return False


def test_all_zmgmt_residents_are_healthy():
    """里程碑前半：Z-MGMT 住戶整組健康才算 up。任一沒起／不健康就失敗。"""
    print("\n=== [#16 里程碑] Z-MGMT 住戶整組健康 ===", flush=True)
    _step(f"docker compose ps，檢查 {sorted(MGMT_HEALTHCHECKED)} 都 running+healthy")
    rows = _compose_ps()
    bad = unhealthy_residents(rows, expected=MGMT_HEALTHCHECKED)
    assert bad == [], f"有 Z-MGMT 住戶未就緒：{bad}"
    _ok("五個有 Docker healthcheck 的住戶全 running+healthy")

    _step("loki 是 distroless，改從 host 探查詢 API :3100/loki/api/v1/labels（補 distroless 的空缺）")
    assert _loki_ready(), "loki 查詢 API 未就緒 —— Loki 未在服務查詢"
    _ok("loki 查詢 API = 200 —— 六個 Z-MGMT 住戶全就緒")


def test_p1_to_p2_handoff_within_zmgmt(events):
    """里程碑後半：Core Event 落地後，Evidence API 能就該 event_id 服務。"""
    print("\n=== [#16 里程碑] P1→P2 handoff（Core Event → Evidence API）===", flush=True)
    mark = events.now()
    _step("注入 5 次 SQLi（P1 入口）")
    for _ in range(5):
        inject_sqli(APP_URL, path="/login", param="username")

    _step("等 P1：管線走完 → receiver 落 Core Event（firing）")
    core = wait_for_event(
        fetch=lambda: events.since(mark),
        match=lambda e: e["scenario_id"] == "sqli-01" and e["lifecycle"] == "firing",
        what="P1 落地的 firing Core Event",
        timeout_s=E2E_TIMEOUT_S,
        poll_s=2.0,
    )
    event_id = core["event_id"]
    _ok(f"P1 交出 event_id={event_id}")

    _step(f"P2：以 blue 身分呼叫 GET /evidence/{event_id}")
    req = urllib.request.Request(
        f"{ENGINE_URL}/evidence/{event_id}",
        headers={"Authorization": f"Bearer {EVIDENCE_TOKEN_BLUE}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 200, f"Evidence API 回 {resp.status}"
        body = json.loads(resp.read())

    print(
        f"       ↳ handoff：event_id={body['event_id']} rule={body.get('rule')} "
        f"line_count={body['line_count']}",
        flush=True,
    )
    # #49：藍隊身分拿到的是匿名標籤，不是真實 rule 名稱。
    assert "technique" not in body
    assert body["event_id"] == event_id, "Evidence API 回的 event_id 與 P1 落地的不符"
    assert body["line_count"] >= 1, "P2 沒取回任何上下文行 —— handoff 斷了"
    _ok("P1→P2 handoff 成立：Core Event 落地後 Evidence API 就該事件取回上下文窗")
