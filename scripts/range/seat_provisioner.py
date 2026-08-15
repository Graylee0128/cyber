#!/usr/bin/env python3
"""Seat Provisioner Agent（#62，WS8 spec §4.1）—— host 側常駐服務，pull 模式建座位。

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

**紅隊、藍隊都交付了**。紅隊一座一台（`main` 端點），藍隊一座兩台
（`a`=DMZ／`b`=內部，見 `build_blue_seat_container`）。兩隊隔離機制刻意
**同款**：共用各自的 VLAN（30／60）＋ OVS `protected=true`，不是 spec §6.4
引用的外部參考架構那種「每 seat 一條獨立 172.31.<X>.0/24 網路」字面做法——
理由見 `build_blue_seat_container` 的 docstring。host 層單一 Falco見
docker-compose.yml／deploy/falco/rules.d/purplescope.yaml；`RED→BLUE`
DMZ-only 防火牆規則見 `_allow_red_to_blue_dmz`。紅隊 seat-to-seat 隔離已在
10.167.223.45 用兩個真的動態紅隊座位互打驗證過：雙向皆 `No route to host`
（OVS 兩個 protected port 之間互不通，機制跟已驗證過的靜態骨架同款），
gateway 仍正常可達——**#62 的四個延伸項目（host Falco、RED→BLUE DMZ、
紅藍座位隔離）全部交付並在真環境驗證過**。

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
    BLUE_IMAGE                 預設 purplescope/blue-seat:latest
    ALLOW_RED_LATERAL／ALLOW_BLUE_LATERAL  同 zones.env，預設關（不放行同隊互打）
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
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from purple.range_zones import load_zones  # noqa: E402

log = logging.getLogger("seat_provisioner")

TTYD_PORT = 7681
CONTAINER_PREFIX = "seat-red-"
BLUE_CONTAINER_PREFIX = "seat-blue-"


@dataclass(frozen=True)
class ProvisionerConfig:
    admission_url: str
    admission_token: str
    poll_seconds: int
    once: bool
    red_image: str
    blue_image: str

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
            blue_image=os.environ.get("BLUE_IMAGE", "purplescope/blue-seat:latest"),
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


def host_if_name(seat_id: str) -> str:
    """host 端 veth 名（<=15 字元，見 attach-red.sh 同款限制）。

    刻意不用內建 `hash()`：它對字串有 per-process 隨機化（`PYTHONHASHSEED`），
    provisioner 重啟後對同一個 `seat_id` 會算出不同名字，讓上一輪的 OVS port
    變孤兒——重啟時 `del-port` 用的是「這輪」算出的新名字，刪不到舊名字那個
    port。實測抓到：連續兩次跑同一個 seat 累積出兩個 `hs*` port。`zlib.crc32`
    跨進程、跨重啟都穩定，同一個 seat_id 永遠算出同一個名字。
    """
    return f"hs{zlib.crc32(seat_id.encode()) % 100000}"


def _attach_container_to_vlan(
    name: str, *, host_if: str, bridge: str, vlan: str, ip: str, gw: str,
    lateral_env: str | None = None,
) -> None:
    """把一個已經用 `--network none` 起好的容器接上某個 VLAN（docker --network
    none → veth → OVS tag → netns 設 IP），紅隊、藍隊共用同一套接法——差別只在
    VLAN／位址／隔離手段，不在網路層的接線手法。沿用 `attach-red.sh`。

    `lateral_env`：非 None 時用 OVS `protected` port 當隔離手段，呼叫端指名要看
    哪個 `ALLOW_*_LATERAL` 環境變數（見 zones.env）——刻意讓呼叫端明講，不用
    vlan 數字反推，vlan id 改了也不會悄悄接錯隔離開關。**紅隊用這個**（seat
    彼此沒有合法互通需求，protected 整批擋掉即可）。

    `lateral_env=None`：不設 protected，隔離改由呼叫端另外用 OVS flow 規則
    做 per-pair 的允許清單。**藍隊用這個**——protected 是整條 bridge 統一生效
    的開關，擋得住跨 seat 互打，但也會連帶擋掉同一個 seat 內 a↔b 的合法橫向
    移動（VM 上實測撞到：a ping b 100% packet loss）。protected 表達不出
    「同 seat 允許、跨 seat 擋」這種依對象而定的規則，只能換 OVS flow
    （見 `_ensure_blue_seat_pair_isolation`／`_allow_seat_pair`）。
    """
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
    if lateral_env is not None and os.environ.get(lateral_env) != "1":
        subprocess.run(["ovs-vsctl", "set", "port", host_if, "protected=true"], check=True)
    subprocess.run(["ip", "link", "set", host_if, "up"], check=True)
    subprocess.run(["ip", "link", "set", "cs0", "netns", name], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "link", "set", "cs0", "name", "eth0"], check=True)
    # 明確指定 MAC——不能靠核心自動配發。VM 上實測抓到：好幾個容器的 eth0
    # 自動配到**同一個** MAC（IPv6 甚至報 dadfailed），導致同 VLAN 內誰都
    # ARP 不到誰，包括 ping 不到自己的 gateway。從 IP 推導、天生跟 IP 一樣
    # 唯一，locally-administered 前綴 `02:00`（IEEE 保留給本地管理位址用）。
    subprocess.run(
        ["ip", "netns", "exec", name, "ip", "link", "set", "eth0", "address", _mac_for_ip(ip)],
        check=True,
    )
    subprocess.run(["ip", "netns", "exec", name, "ip", "addr", "add", f"{ip}/24", "dev", "eth0"], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "link", "set", "eth0", "up"], check=True)
    subprocess.run(["ip", "netns", "exec", name, "ip", "link", "set", "lo", "up"], check=True)
    subprocess.run(
        ["ip", "netns", "exec", name, "ip", "route", "add", "default", "via", gw],
        capture_output=True, check=False,
    )


def _mac_for_ip(ip: str) -> str:
    """從 IP 位址推導一個保證唯一的 MAC——跟 IP 一樣唯一，不必另外維護一份
    配發紀錄。`02` 前綴是 IEEE 保留給 locally-administered 位址的其中一種
    合法值（第二個位元組的第二個 bit 是 1、broadcast bit 是 0）。"""
    octets = [int(x) for x in ip.split(".")]
    return "02:00:" + ":".join(f"{o:02x}" for o in octets)


def _next_free_ip(prefix: str, first: int, existing_ips: set[str], *, count: int = 1) -> list[str]:
    found: list[str] = []
    for host in range(first, 255):
        candidate = f"{prefix}.{host}"
        if candidate not in existing_ips and candidate not in found:
            found.append(candidate)
        if len(found) == count:
            return found
    raise RuntimeError(f"no free address left from {prefix}.{first} onward")


_KNOWN_IMAGE_DOCKERFILES = {
    "purplescope/red-attacker:latest": "deploy/red-attacker",
    "purplescope/blue-seat:latest": "deploy/blue-seat",
}


def _ensure_image_built(image: str) -> None:
    """實測抓到的真缺口：`attach-red.sh` 有自建 `purplescope/red-attacker`
    的 fallback，但這支（provisioner 建座位容器的唯一路徑）本身沒有——藍隊
    的 `purplescope/blue-seat:latest` 從來沒有任何腳本會去 build，
    `docker run` 直接炸 exit 125（image 不存在，也不是可 pull 的公開
    image）。只對已知的兩個內建 image 自動補 build，呼叫端自訂 image 就
    不猜測，交給呼叫端自己準備好（同 attach-red.sh 的界線）。
    """
    dockerfile_dir = _KNOWN_IMAGE_DOCKERFILES.get(image)
    if dockerfile_dir is None:
        return
    if subprocess.run(["docker", "image", "inspect", image],
                       capture_output=True, check=False).returncode == 0:
        return
    repo_root = Path(__file__).resolve().parents[2]
    log.info("image %s 不存在，自動 build（%s）", image, dockerfile_dir)
    subprocess.run(["docker", "build", "-t", image, str(repo_root / dockerfile_dir)], check=True)


def build_red_seat_container(
    seat_id: str, *, image: str, zones: dict[str, str], existing_ips: set[str],
) -> dict[str, Any]:
    """建一個紅隊座位容器，接上 Z-RED（VLAN30），回傳 admission `/ready` 要的
    endpoints 格式：`[{"terminal": "main", "host": ip, "port": 7681}]`
    （見 `AdmissionService.validate_endpoints`：紅隊固定一個 `main` 端點）。

    差別是**動態找下一個空位址**，不是像 attach-red.sh 那樣從 COUNT 固定重建
    整批——這正是 #62 的「pull 模式」與原本 G2 一次性腳本的不同之處。
    """
    _ensure_image_built(image)
    prefix, _, first_str = zones["RED_IP_FIRST"].rpartition(".")
    (ip,) = _next_free_ip(prefix, int(first_str), existing_ips)

    name = f"{CONTAINER_PREFIX}{seat_id}"
    host_if = host_if_name(seat_id)

    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "none",
         "--cap-add", "NET_ADMIN", image, "sleep", "infinity"],
        check=True, capture_output=True,
    )
    _attach_container_to_vlan(
        name, host_if=host_if, bridge=zones["RANGE_BRIDGE"], vlan=zones["Z_RED_VLAN"],
        ip=ip, gw=zones["Z_RED_GW"], lateral_env="ALLOW_RED_LATERAL",
    )
    subprocess.Popen(
        ["ip", "netns", "exec", name, "python3",
         str(Path(__file__).with_name("stub_listener.py")), "--ports", str(TTYD_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    return {"endpoints": [{"terminal": "main", "host": ip, "port": TTYD_PORT}]}


def _ensure_blue_seat_pair_isolation(bridge: str, blue_cidr: str) -> None:
    """VLAN60 預設擋掉同段互打；per-seat 的 a↔b 例外由 `_allow_seat_pair()`
    個別開。`ovs-ofctl add-flow` 對同一個 match+priority 是**替換**不是疊加，
    天生冪等——provisioner 每次啟動都可以放心重跑，不用先查有沒有這條規則。

    為什麼不繼續用 `protected=true`（v1 原本的決定）：那個開關是整條 bridge
    統一生效，擋得住跨 seat 互打，但也連帶擋掉同一個 seat 內 a↔b 的合法橫向
    移動——VM 上實測撞到（a ping b 100% packet loss），protected 表達不出
    「同 seat 允許、跨 seat 擋」這種依對象而定的規則，只有 flow 規則做得到。

    `ALLOW_BLUE_LATERAL=1`（zones.env 的收尾期開關）在這裡生效：flow 規則是
    藍隊**唯一**的隔離機制（`run()` 不對 Z-BLUE 下 DOCKER-USER 規則），所以
    這個開關也只能在這裡兌現。開啟時不只是「不裝」預設 deny，還要把上一輪
    可能留下的那條刪掉——flow 存活於 bridge、跨 provisioner 重啟不會自己消失，
    只跳過安裝的話開關按了等於沒按。`--strict` 讓刪除限定在同 match＋同
    priority 的那一條，不會連帶掃掉 `_allow_seat_pair()` 的 per-pair 白名單。
    """
    flow = f"priority=100,ip,nw_src={blue_cidr},nw_dst={blue_cidr}"
    if os.environ.get("ALLOW_BLUE_LATERAL") == "1":
        subprocess.run(
            ["ovs-ofctl", "--strict", "del-flows", bridge, flow],
            capture_output=True, check=False,
        )
        return
    subprocess.run(
        ["ovs-ofctl", "add-flow", bridge, f"{flow},actions=drop"],
        check=True, capture_output=True,
    )


def _allow_seat_pair(bridge: str, ip_a: str, ip_b: str) -> None:
    """開一對藍隊座位的 a↔b 雙向白名單，優先權高於上面的預設 deny。"""
    for src, dst in ((ip_a, ip_b), (ip_b, ip_a)):
        subprocess.run(
            ["ovs-ofctl", "add-flow", bridge,
             f"priority=200,ip,nw_src={src},nw_dst={dst},actions=normal"],
            check=True, capture_output=True,
        )


def _revoke_ip_flows(bridge: str, ip: str) -> None:
    """座位釋放、IP 要回收重新分配前，清掉所有引用這個 IP 的 flow——不清的話，
    IP 被下一個座位撿到時，舊規則會讓兩個完全不相干的座位（新舊各一半）
    意外連得到彼此。孤兒回收時對每個被拆的藍隊容器呼叫。"""
    for direction in ("nw_src", "nw_dst"):
        subprocess.run(
            ["ovs-ofctl", "del-flows", bridge, f"ip,{direction}={ip}"],
            capture_output=True, check=False,
        )


def _allow_red_to_blue_dmz(router_ns: str, ip_a: str) -> None:
    """開放 RED→BLUE 到這個座位的 DMZ 主機 a（build-range.sh 宣告的空集合
    `blue_dmz_ips` 動態補成員）。刻意**只**加 a，b 永遠不進這個集合——WS8
    spec §6.3 要的是「打得到 DMZ、要打內部得自己橫向移動」，開 b 等於免費
    跳過那一步。`nft add element` 對已存在的成員是 no-op，冪等，provisioner
    每輪重跑都可以放心呼叫。"""
    subprocess.run(
        ["ip", "netns", "exec", router_ns, "nft", "add", "element", "inet", "range_fw",
         "blue_dmz_ips", "{", ip_a, "}"],
        check=True, capture_output=True,
    )


def _revoke_red_to_blue_dmz(router_ns: str, ip_a: str) -> None:
    """座位釋放時把 a 的位址從 `blue_dmz_ips` 拿掉——跟 `_revoke_ip_flows`
    同一個理由：IP 被下一個座位撿走前不清掉，RED 會意外打得到新座位的 DMZ，
    即使新座位根本沒有把自己的 a 位址加進來過。"""
    subprocess.run(
        ["ip", "netns", "exec", router_ns, "nft", "delete", "element", "inet", "range_fw",
         "blue_dmz_ips", "{", ip_a, "}"],
        capture_output=True, check=False,
    )


def build_blue_seat_container(
    seat_id: str, *, image: str, zones: dict[str, str], existing_ips: set[str],
) -> dict[str, Any]:
    """建一個藍隊座位的兩台容器（a=DMZ、b=內部，WS8 spec §6.1），接上 Z-BLUE
    （VLAN60）。回傳 `[{"terminal":"a",...}, {"terminal":"b",...}]`（見
    `AdmissionService.validate_endpoints`：藍隊固定兩個端點 a／b）。

    **隔離機制**：共用 VLAN60（不是 spec §6.4 引用的外部參考架構那種「每
    seat 一條獨立 172.31.<X>.0/24 網路」字面做法——validate_endpoints() 已經
    寫死 blue endpoint 必須落在 10.167.60.0/24，跟「獨立網路」字面衝突），
    但**不用** `protected=true`（見 `_ensure_blue_seat_pair_isolation` 的
    docstring：那個開關表達不出「同 seat 允許、跨 seat 擋」）。改用 OVS flow
    規則做 per-pair 白名單，預設 deny、只放行自己這一對。

    真的 Z-BLUE image（`purplescope/blue-seat:latest`）CMD 本身就是 ttyd，
    不像紅隊要另外 Popen 一個 stub_listener.py。

    **RED→BLUE DMZ-only**：這裡也順手把 a 位址加進 `blue_dmz_ips`
    （`_allow_red_to_blue_dmz`）——build-range.sh 的 nft 規則只放行到這個
    集合裡的位址，b 永遠不在集合內。這是 build-range.sh 自己註解點名
    「由 #62 補」的那個缺口（見該檔 nft 區塊），現在補上。
    """
    _ensure_image_built(image)
    prefix, _, first_str = zones["BLUE_IP_FIRST"].rpartition(".")
    ip_a, ip_b = _next_free_ip(prefix, int(first_str), existing_ips, count=2)
    bridge = zones["RANGE_BRIDGE"]

    endpoints = []
    for terminal, ip in (("a", ip_a), ("b", ip_b)):
        name = f"{BLUE_CONTAINER_PREFIX}{terminal}-{seat_id}"
        host_if = host_if_name(f"{seat_id}:{terminal}")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(
            ["docker", "run", "-d", "--name", name, "--network", "none", image],
            check=True, capture_output=True,
        )
        _attach_container_to_vlan(
            name, host_if=host_if, bridge=bridge, vlan=zones["Z_BLUE_VLAN"],
            ip=ip, gw=zones["Z_BLUE_GW"],
        )
        endpoints.append({"terminal": terminal, "host": ip, "port": TTYD_PORT})

    _allow_seat_pair(bridge, ip_a, ip_b)
    _allow_red_to_blue_dmz(zones["RANGE_ROUTER_NS"], ip_a)
    return {"endpoints": endpoints}


def _seat_id_from_red_name(name: str) -> str:
    return name[len(CONTAINER_PREFIX):]


def _seat_id_from_blue_name(name: str) -> str:
    # seat-blue-a-<seat_id> / seat-blue-b-<seat_id>：terminal 是固定一個字元
    # 加一個連字號，前綴之後直接切掉那兩個字元就是 seat_id。
    return name[len(BLUE_CONTAINER_PREFIX) + 2:]


def _scan_container_ips(name_prefix: str, ip_prefix: str) -> set[str]:
    """掃現有名字符合 `name_prefix` 的容器實際掛的 IP——供 IP 分配避開已占用
    位址，紅隊、藍隊共用（藍隊一個 seat 兩台容器，各自的 IP 都會被掃到，
    `_next_free_ip` 才不會把 a、b 分到同一個位址）。"""
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name_prefix}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    ips: set[str] = set()
    for name in result.stdout.splitlines():
        addr = subprocess.run(
            ["ip", "netns", "exec", name, "ip", "-4", "-o", "addr", "show", "eth0"],
            capture_output=True, text=True, check=False,
        ).stdout
        if addr and ip_prefix in addr:
            ips.add(next(tok.split("/")[0] for tok in addr.split() if tok.startswith(ip_prefix)))
    return ips


def currently_provisioned_ips(zones: dict[str, str]) -> set[str]:
    prefix, _, _ = zones["RED_IP_FIRST"].rpartition(".")
    return _scan_container_ips(CONTAINER_PREFIX, prefix)


def currently_provisioned_blue_ips(zones: dict[str, str]) -> set[str]:
    prefix, _, _ = zones["BLUE_IP_FIRST"].rpartition(".")
    return _scan_container_ips(BLUE_CONTAINER_PREFIX, prefix)


def _container_ip(name: str, ip_prefix: str) -> str | None:
    """讀一個容器 eth0 目前的 IP（拆容器前呼叫，容器不在了就讀不到）。"""
    addr = subprocess.run(
        ["ip", "netns", "exec", name, "ip", "-4", "-o", "addr", "show", "eth0"],
        capture_output=True, text=True, check=False,
    ).stdout
    if addr and ip_prefix in addr:
        return next(tok.split("/")[0] for tok in addr.split() if tok.startswith(ip_prefix))
    return None


def _sweep_orphans_for(
    *, name_prefix: str, seat_id_from_name, active_seat_ids: set[str], on_remove=None,
) -> int:
    """孤兒回收的共用引擎。刻意用 `active`（requested＋ready＋claimed）而不是
    `pending`（只有 requested）當判準——已經 `ready`／`claimed` 的座位是正常
    在用的座位，不是孤兒；只看 pending 會在每次重啟時把所有剛建好、正在
    服務玩家的容器一起砍掉（v1 的真實 bug，見 PR 說明）。

    `on_remove(name)`：**拆容器前**呼叫（此時容器還在、IP 還讀得到）——藍隊
    用它先記下 IP，容器真的被拆之後再清 OVS flow 白名單（見 `sweep_orphans`）。
    紅隊不需要，留 None。
    """
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={name_prefix}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    names = [n for n in result.stdout.splitlines() if n]
    removed = 0
    for name in names:
        seat_id = seat_id_from_name(name)
        if seat_id in active_seat_ids:
            continue  # 座位還算數（requested/ready/claimed），容器留著
        if on_remove is not None:
            on_remove(name)  # 容器還在，IP 還讀得到，拆之前先處理
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["ip", "netns", "del", name], capture_output=True, check=False)
        removed += 1
        log.info("swept orphan seat container %s (seat %s no longer active)", name, seat_id)
    return removed


def sweep_orphans(client: AdmissionClient, *, zones: dict[str, str] | None = None) -> int:
    """provisioner 重啟後的孤兒回收：紅隊（`seat-red-*`）與藍隊（`seat-blue-*`）
    各掃各的——不碰 `range-red*`（#78 spike 用的舊命名，不是同一批）。

    `zones`：可選——只有清藍隊 OVS flow 白名單／RED→BLUE DMZ 白名單需要知道
    bridge／router netns 名字與 IP prefix。單元測試不帶 zones 時退化成不清、
    不動（純孤兒回收邏輯照樣測），生產路徑（`run()`）一定會傳。
    """
    bridge = (zones or {}).get("RANGE_BRIDGE", "br-range")
    router_ns = (zones or {}).get("RANGE_ROUTER_NS", "ns-router")
    blue_prefix, _, _ = (zones or {}).get("BLUE_IP_FIRST", "0.0.0.0").rpartition(".")

    def _clean_blue_flows(name: str) -> None:
        if zones is None:
            return
        ip = _container_ip(name, blue_prefix)
        if ip is None:
            return
        _revoke_ip_flows(bridge, ip)
        # 只有 a（DMZ）進過 blue_dmz_ips，b 從沒加過——刪不存在的成員 nft 會
        # 回非 0（"No such file or directory"），但 `_revoke_red_to_blue_dmz`
        # 本來就 check=False 吞掉這個結果（跟 _revoke_ip_flows 同款），不必
        # 為了省這一次呼叫特地判斷 terminal 字元，兩台都呼叫比多一個分支簡單。
        _revoke_red_to_blue_dmz(router_ns, ip)

    return _sweep_orphans_for(
        name_prefix=CONTAINER_PREFIX, seat_id_from_name=_seat_id_from_red_name,
        active_seat_ids=client.active_seat_ids("red"),
    ) + _sweep_orphans_for(
        name_prefix=BLUE_CONTAINER_PREFIX, seat_id_from_name=_seat_id_from_blue_name,
        active_seat_ids=client.active_seat_ids("blue"), on_remove=_clean_blue_flows,
    )


def _default_red_builder(seat_id: str, *, image: str, zones: dict[str, str]) -> dict[str, Any]:
    return build_red_seat_container(
        seat_id, image=image, zones=zones, existing_ips=currently_provisioned_ips(zones),
    )


def _default_blue_builder(seat_id: str, *, image: str, zones: dict[str, str]) -> dict[str, Any]:
    return build_blue_seat_container(
        seat_id, image=image, zones=zones, existing_ips=currently_provisioned_blue_ips(zones),
    )


def _ensure_docker_user_drop(cidr: str, lateral_env: str) -> None:
    """同隊互打的第二層防線（跟 OVS `protected` 是同一個決定的兩份保險，見
    `attach-red.sh` 對 Z-RED 的同款規則）：`protected=true` 擋的是走 br-range
    veth 那條路徑；這條擋的是萬一流量改道進了 docker forward path。冪等
    （`-C` 先查再 `-I`），provisioner 每次啟動都跑一次，不用另外開關腳本。

    **只給紅隊用**：這條是整段 CIDR 的 src+dst DROP，沒有 per-pair 例外，
    只有在「同段內沒有任何合法互通」時才等價於 `protected`。紅隊成立，藍隊
    不成立（同 seat a↔b 必須通，WS8 spec §6.1），所以 `run()` 不對 Z-BLUE
    呼叫這支——理由見那裡的註解。
    """
    if os.environ.get(lateral_env) == "1":
        return
    check = subprocess.run(["iptables", "-nL", "DOCKER-USER"], capture_output=True, check=False)
    if check.returncode != 0:
        return  # DOCKER-USER chain 不存在（docker 還沒建過 bridge network），沒東西可插
    exists = subprocess.run(
        ["iptables", "-C", "DOCKER-USER", "-s", cidr, "-d", cidr,
         "-m", "comment", "--comment", "purplescope-seat-isolation", "-j", "DROP"],
        capture_output=True, check=False,
    )
    if exists.returncode != 0:
        subprocess.run(
            ["iptables", "-I", "DOCKER-USER", "1", "-s", cidr, "-d", cidr,
             "-m", "comment", "--comment", "purplescope-seat-isolation", "-j", "DROP"],
            check=True, capture_output=True,
        )


def run(
    config: ProvisionerConfig,
    *,
    client: AdmissionClient | None = None,
    red_builder=_default_red_builder,
    blue_builder=_default_blue_builder,
    sleep=time.sleep,
    ensure_isolation=_ensure_docker_user_drop,
    ensure_blue_flow_isolation=_ensure_blue_seat_pair_isolation,
) -> None:
    """輪詢主迴圈。`client`／`red_builder`／`blue_builder`／`sleep`／
    `ensure_isolation`／`ensure_blue_flow_isolation` 都可覆寫——單元測試餵假
    的，不必真的連 admission、不必真的有 docker/OVS/iptables（見
    tests/topology/test_seat_provisioner.py）。生產路徑用預設值（真 HTTP、
    真容器、真防火牆規則）。"""
    admission = client or AdmissionClient(config.admission_url, config.admission_token)
    zones = load_zones()
    prefix = zones["RANGE_NET_PREFIX"]
    ensure_isolation(f"{prefix}.{zones['Z_RED_VLAN']}.0/24", "ALLOW_RED_LATERAL")
    # 藍隊**刻意沒有** DOCKER-USER 第二層：那條規則是整段 CIDR 的 src+dst
    # DROP，表達不出「同 seat 的 a↔b 允許、跨 seat 擋」（WS8 spec §6.1 要求
    # 同座位橫向移動可行），也沒有 per-pair 例外可加。紅隊擋整段符合意圖
    # （seat 之間本來就沒有合法互通），藍隊擋整段會直接違反規格。今天 br-range
    # 是 OVS bridge、同段流量走 L2 不進 FORWARD/DOCKER-USER，所以那條規則是
    # 沒作用的死規則——但只要 br_netfilter 被載入、或藍隊路徑改掛 docker
    # bridge network，同 seat 的 a↔b 就會無聲斷掉（正是 #118 花時間查過的
    # Bug 1 長相）。藍隊的隔離機制單一：下面的 OVS flow 白名單。
    ensure_blue_flow_isolation(zones["RANGE_BRIDGE"], f"{prefix}.{zones['Z_BLUE_VLAN']}.0/24")
    swept = sweep_orphans(admission, zones=zones)
    if swept:
        log.info("swept %d orphan seat container(s) on startup", swept)

    builders = {"red": (red_builder, config.red_image), "blue": (blue_builder, config.blue_image)}

    while True:
        for team, (builder, image) in builders.items():
            try:
                pending = admission.pending(team)
            except Exception:  # noqa: BLE001 — 輪詢失敗不該讓常駐服務死掉，下一輪再試
                log.exception("failed to poll pending %s seats", team)
                pending = []

            for seat in pending:
                seat_id = seat["seat_id"]
                try:
                    built = builder(seat_id, image=image, zones=zones)
                except Exception:  # noqa: BLE001 — 見檔頭：失敗留給 §4.4 逾時重試處理
                    log.exception("failed to provision %s seat %s", team, seat_id)
                    continue
                if admission.mark_ready(seat_id, built["endpoints"]):
                    log.info("%s seat %s ready at %s", team, seat_id, built["endpoints"])

        if config.once:
            return
        sleep(config.poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run(ProvisionerConfig.from_env())


if __name__ == "__main__":
    main()
