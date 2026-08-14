#!/usr/bin/env python3
"""Seat Provisioner Agent（#62，WS8 spec §4.1）—— host 側常駐服務，pull 模式建紅隊座位。

```
中控寫入 seat(state=requested)
        ↓  （本檔輪詢，admission 不主動呼叫）
host 側 Seat Provisioner Agent（這裡）
        ↓
建立容器／網路 → POST /admission/seats/{id}/ready
```

**為什麼是 pull 不是 push**：WS5 spec §2.3 已經拒絕過「API 直接驅動 host 腳本」——
那等於讓 Z-APP 的服務擁有操作 host 上 OVS 的權限。反過來，由 host 側主動輪詢
admission 的 `GET /admission/seats/pending`，跟契約 2（TARGET → MGMT 單向、
response 走 agent pull）同一條邏輯：**只有已經有 host 權限的一方能發起連線**。

**只交付紅隊路徑**。藍隊（Z-BLUE：一段兩台、受限 shell、sudo allowlist、host 層
單一 Falco）需要獨立的 image 與網路隔離設計，是後續階段，本檔呼叫 `pending()`
時只帶 `team=red`，不動藍隊那半。

**失敗路徑刻意不在這裡處理**：容器建立失敗就什麼都不做，seat 留在 `requested`。
admission 既有的 `expire_requested()`（`sweeper.py`，§4.4 逾時三段式）會在 T 秒後
自動重試一次、再失敗才釋放＋告警——那條路徑已經有人在管，provisioner 不用重造。

用法：
    python3 scripts/range/seat_provisioner.py

環境變數：
    ADMISSION_URL              e.g. http://localhost:8000
    ADMISSION_PROVISIONER_TOKEN 借用既有 instructor token（#62 v1 決定，見 PR 說明）
    PROVISIONER_POLL_SECONDS   預設 3
    PROVISIONER_ONCE           設 1 只跑一輪（測試用）
    RED_IMAGE                  預設 purplescope/red-attacker:latest（同 attach-red.sh）
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from purple.range_zones import load_zones  # noqa: E402

log = logging.getLogger("seat_provisioner")

TTYD_PORT = 7681
CONTAINER_PREFIX = "seat-red-"


@dataclass(frozen=True)
class ProvisionerConfig:
    admission_url: str
    admission_token: str
    poll_seconds: int
    once: bool
    red_image: str

    @classmethod
    def from_env(cls) -> "ProvisionerConfig":
        url = os.environ.get("ADMISSION_URL")
        token = os.environ.get("ADMISSION_PROVISIONER_TOKEN")
        if not url or not token:
            raise ValueError("ADMISSION_URL and ADMISSION_PROVISIONER_TOKEN are required")
        return cls(
            admission_url=url.rstrip("/"),
            admission_token=token,
            poll_seconds=int(os.environ.get("PROVISIONER_POLL_SECONDS", "3")),
            once=os.environ.get("PROVISIONER_ONCE") == "1",
            red_image=os.environ.get("RED_IMAGE", "purplescope/red-attacker:latest"),
        )


class AdmissionClient:
    """薄 HTTP 殼，唯一責任是帶 Bearer token 打 admission API。純函數化以外的
    副作用（網路 I/O）集中在這一個類別，`run()` 的迴圈邏輯才好測。"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None

    def pending(self, team: str) -> list[dict]:
        return self._request("GET", f"/admission/seats/pending?team={team}") or []

    def active_seat_ids(self, team: str) -> set[str]:
        return set(self._request("GET", f"/admission/seats/active?team={team}") or [])

    def mark_ready(self, seat_id: str, endpoints: list[dict]) -> bool:
        try:
            self._request("POST", f"/admission/seats/{seat_id}/ready", {"endpoints": endpoints})
            return True
        except urllib.error.HTTPError as exc:
            # 404＝座位已經不在等待就緒（可能被別的輪次搶先建好或已釋放）；
            # 422＝endpoint 驗證沒過（見 AdmissionService.validate_endpoints）。
            # 兩種都不是「重試一次」能解決的，往上噴讓呼叫端記 log 就好，
            # 不吞掉——吞掉會讓建置失敗的 seat 靜靜卡住，直到逾時掃描才發現。
            log.warning("ready(%s) rejected: %s", seat_id, exc)
            return False


def build_red_seat_container(
    seat_id: str, *, image: str, zones: dict[str, str], existing_ips: set[str],
) -> dict[str, Any]:
    """建一個紅隊座位容器，接上 Z-RED（VLAN30），回傳 admission `/ready` 要的
    endpoints 格式：`[{"terminal": "main", "host": ip, "port": 7681}]`
    （見 `AdmissionService.validate_endpoints`：紅隊固定一個 `main` 端點）。

    沿用 `attach-red.sh` 的手法（docker --network none → veth → OVS tag=VLAN30
    → netns 設 IP），差別是**動態找下一個空位址**，不是像原腳本那樣從 COUNT
    固定重建整批——這正是 #62 的「pull 模式」與原本 G2 一次性腳本的不同之處。
    """
    bridge = zones["RANGE_BRIDGE"]
    vlan = zones["Z_RED_VLAN"]
    gw = zones["Z_RED_GW"]
    prefix, _, first_str = zones["RED_IP_FIRST"].rpartition(".")
    first = int(first_str)

    ip = next(
        f"{prefix}.{host}"
        for host in range(first, 255)
        if f"{prefix}.{host}" not in existing_ips
    )

    name = f"{CONTAINER_PREFIX}{seat_id}"
    host_if = f"hs{abs(hash(seat_id)) % 100000}"  # <=15 字元，見 attach-red.sh 同款限制

    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "none",
         "--cap-add", "NET_ADMIN", image, "sleep", "infinity"],
        check=True, capture_output=True,
    )
    pid = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Pid}}", name],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    netns_link = f"/var/run/netns/{name}"
    subprocess.run(["mkdir", "-p", "/var/run/netns"], check=True)
    subprocess.run(["ln", "-sf", f"/proc/{pid}/ns/net", netns_link], check=True)

    subprocess.run(["ip", "link", "del", host_if], capture_output=True, check=False)
    subprocess.run(["ip", "link", "add", host_if, "type", "veth", "peer", "name", "cs0"], check=True)
    subprocess.run(["ovs-vsctl", "--if-exists", "del-port", bridge, host_if], check=True)
    subprocess.run(["ovs-vsctl", "add-port", bridge, host_if, f"tag={vlan}"], check=True)
    subprocess.run(["ovs-vsctl", "set", "port", host_if, "protected=true"], check=True)
    subprocess.run(["ip", "link", "set", host_if, "up"], check=True)
    subprocess.run(["ip", "link", "set", "cs0", "netns", name], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "link", "set", "cs0", "name", "eth0"], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "addr", "add", f"{ip}/24", "dev", "eth0"], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "link", "set", "eth0", "up"], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "link", "set", "lo", "up"], check=True)
    subprocess.run(
        ["ip", "netns", "exec", name, "ip", "route", "add", "default", "via", gw],
        capture_output=True, check=False,
    )
    subprocess.Popen(
        ["ip", "netns", "exec", name, "python3",
         str(Path(__file__).with_name("stub_listener.py")), "--ports", str(TTYD_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    return {"endpoints": [{"terminal": "main", "host": ip, "port": TTYD_PORT}]}


def currently_provisioned_ips(zones: dict[str, str]) -> set[str]:
    """掃現有 `seat-red-*` 容器實際掛的 IP——供 IP 分配避開已占用位址，
    也是孤兒回收的資料來源（見 `sweep_orphans`）。"""
    prefix, _, _ = zones["RED_IP_FIRST"].rpartition(".")
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    ips: set[str] = set()
    for name in result.stdout.splitlines():
        seat_id = name[len(CONTAINER_PREFIX):]
        addr = subprocess.run(
            ["ip", "netns", "exec", name, "ip", "-4", "-o", "addr", "show", "eth0"],
            capture_output=True, text=True, check=False,
        ).stdout
        if addr and prefix in addr:
            ips.add(next(tok.split("/")[0] for tok in addr.split() if tok.startswith(prefix)))
        del seat_id
    return ips


def sweep_orphans(client: AdmissionClient) -> int:
    """provisioner 重啟後的孤兒回收：座位已經**不算數**（released／failed，
    不在 `list_active` 裡）但容器還留著，就地拆掉。

    刻意用 `active`（requested＋ready＋claimed）而不是 `pending`（只有
    requested）當判準——已經 `ready`／`claimed` 的座位是正常在用的座位，
    不是孤兒；只看 pending 會在每次重啟時把所有剛建好、正在服務玩家的
    容器一起砍掉。**只處理 provisioner 自己命名的 `seat-red-*` 容器**——
    不碰 `range-red*`（#78 spike 用的舊命名，不是同一批）。
    """
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    names = [n for n in result.stdout.splitlines() if n]
    if not names:
        return 0
    active_seat_ids = client.active_seat_ids("red")
    removed = 0
    for name in names:
        seat_id = name[len(CONTAINER_PREFIX):]
        if seat_id in active_seat_ids:
            continue  # 座位還算數（requested/ready/claimed），容器留著
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["ip", "netns", "del", name], capture_output=True, check=False)
        removed += 1
        log.info("swept orphan seat container %s (seat %s no longer active)", name, seat_id)
    return removed


def _default_builder(seat_id: str, *, image: str, zones: dict[str, str]) -> dict[str, Any]:
    return build_red_seat_container(
        seat_id, image=image, zones=zones, existing_ips=currently_provisioned_ips(zones),
    )


def run(
    config: ProvisionerConfig,
    *,
    client: AdmissionClient | None = None,
    builder=_default_builder,
    sleep=time.sleep,
) -> None:
    """輪詢主迴圈。`client`／`builder`／`sleep` 都可覆寫——單元測試餵假的，
    不必真的連 admission、不必真的有 docker/OVS（見 tests/range/
    test_seat_provisioner.py）。生產路徑用預設值（真 HTTP、真容器）。"""
    admission = client or AdmissionClient(config.admission_url, config.admission_token)
    zones = load_zones()
    swept = sweep_orphans(admission)
    if swept:
        log.info("swept %d orphan seat container(s) on startup", swept)

    while True:
        try:
            pending = admission.pending("red")
        except Exception:  # noqa: BLE001 — 輪詢失敗不該讓常駐服務死掉，下一輪再試
            log.exception("failed to poll pending seats")
            pending = []

        for seat in pending:
            seat_id = seat["seat_id"]
            try:
                built = builder(seat_id, image=config.red_image, zones=zones)
            except Exception:  # noqa: BLE001 — 見檔頭：失敗留給 §4.4 逾時重試處理
                log.exception("failed to provision seat %s", seat_id)
                continue
            if admission.mark_ready(seat_id, built["endpoints"]):
                log.info("seat %s ready at %s", seat_id, built["endpoints"])

        if config.once:
            return
        sleep(config.poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run(ProvisionerConfig.from_env())


if __name__ == "__main__":
    main()
