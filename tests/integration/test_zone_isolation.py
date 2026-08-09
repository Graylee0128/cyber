"""票 #15 —— 四區網段隔離的**實測**（需 docker compose 全棧，`-m integration`）。

`tests/topology/test_zone_model.py` 驗證的是「compose 檔宣告的區對不對」（純函式，
不需 docker）。這裡驗證的是「Docker 真的照著隔離」：

- app（z-target）**連不到** postgres（z-mgmt）—— 無共享 network，連 DNS 都不解析。
  這是 Docker membership 能強制、也是本票交付的隔離證據。
- 對照組：app **連得到** alloy（同在 z-target 的 collector）—— 證明上面的 UNREACHABLE
  是真隔離，不是探針本身壞掉。

  註（票 #11）：正對照原本用 prometheus，但 OTLP push 上線後 prometheus 已移出
  z-target（只留 z-mgmt），app 本就**該**連不到它了 —— 那是契約 2（MGMT→TARGET
  不可達）的體現。故正對照改用仍與 app 同區的 alloy。

逐條 echo，方便在 CI log 對照。compose 做不到的（契約 2 方向、真防火牆）委派 #13。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from purple.topology_check import check_zone_assignments

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

# app 容器內用 python 探一個 host:port。可達 exit 0，不可達（含 DNS 不解析）exit 1。
_PROBE = (
    "import socket,sys\n"
    "host,port=sys.argv[1],int(sys.argv[2])\n"
    "try:\n"
    "    s=socket.socket();s.settimeout(3);s.connect((host,port));s.close()\n"
    "    print('REACHABLE');sys.exit(0)\n"
    "except Exception as e:\n"
    "    print('UNREACHABLE',type(e).__name__);sys.exit(1)\n"
)


def _step(msg: str) -> None:
    print(f"    ▶ {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"    ✅ {msg}", flush=True)


def _probe_from_app(host: str, port: int) -> subprocess.CompletedProcess:
    """在 vulnerable-app 容器內，試連 host:port。"""
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "vulnerable-app",
         "python", "-c", _PROBE, host, str(port)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )


def test_compose_runtime_config_is_zone_compliant():
    """對 docker 解析後的實際 config（非只 YAML 檔）再驗一次區歸屬。"""
    print("\n=== [#15 區歸屬] docker 解析後的 config 符合可強制的區規則 ===", flush=True)
    raw = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert raw.returncode == 0, raw.stderr
    cfg = json.loads(raw.stdout)
    service_networks = {
        name: set((svc.get("networks") or {}).keys())
        for name, svc in cfg["services"].items()
    }
    _step(f"解析出 {len(service_networks)} 個服務的區歸屬")
    fails = check_zone_assignments(service_networks)
    assert fails == [], f"區歸屬違規：{fails}"
    _ok("所有服務的區歸屬通過（mgmt 住戶在 z-mgmt、collector 在 z-target、RED⊥MGMT）")


def test_app_in_target_zone_cannot_reach_mgmt_postgres():
    """z-target 的 app 連不到 z-mgmt 的 postgres —— 本票交付的隔離。"""
    print("\n=== [#15 隔離] z-target(app) → z-mgmt(postgres) 應不可達 ===", flush=True)
    _step("在 app 容器內試連 postgres:5432（app 只在 z-target，postgres 只在 z-mgmt）")
    result = _probe_from_app("postgres", 5432)
    print(f"       ↳ 探針輸出：{result.stdout.strip()!r} exit={result.returncode}", flush=True)
    assert result.returncode != 0, (
        "app 竟連得到 mgmt 的 postgres —— 四區隔離破了（兩者不該有共享 network）"
    )
    _ok("app 連不到 mgmt postgres —— Docker network membership 強制隔離成立")


def test_app_can_reach_same_zone_alloy_as_positive_control():
    """對照組：app 連得到同在 z-target 的 alloy collector —— 證明探針本身會通。"""
    print("\n=== [#15 對照] z-target(app) → z-target(alloy) 應可達 ===", flush=True)
    _step("在 app 容器內試連 alloy:12345（兩者都在 z-target；alloy 是 collector）")
    result = _probe_from_app("alloy", 12345)
    print(f"       ↳ 探針輸出：{result.stdout.strip()!r} exit={result.returncode}", flush=True)
    assert result.returncode == 0, (
        f"app 連不到同區的 alloy，探針或網段設定有問題：{result.stdout} {result.stderr}"
    )
    _ok("app 連得到同區 alloy —— 上面的不可達是真隔離，不是探針壞掉")


def test_app_cannot_reach_prometheus_after_otlp_move():
    """票 #11 的體現：prometheus 移出 z-target 後，app **連不到**它了。

    這正是契約 2（MGMT→TARGET 不可達）在 compose membership 層第一次真的成立 ——
    以前 prometheus 為了 scrape 得上 z-target（那道疤），OTLP push 上線後拔掉。
    """
    print("\n=== [#11] z-target(app) → z-mgmt(prometheus) 應不可達 ===", flush=True)
    _step("在 app 容器內試連 prometheus:9090（prometheus 已只在 z-mgmt）")
    result = _probe_from_app("prometheus", 9090)
    print(f"       ↳ 探針輸出：{result.stdout.strip()!r} exit={result.returncode}", flush=True)
    assert result.returncode != 0, (
        "app 竟連得到 prometheus —— OTLP 上線後它不該還在 z-target（契約 2 該擋）"
    )
    _ok("app 連不到 prometheus —— OTLP push 讓 scrape 疤消失，契約 2 在此成立")
