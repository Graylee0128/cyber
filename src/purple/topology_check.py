"""拓樸契約檢查的可匯入邏輯（票 12 / issue #13）。

CLI 在 `scripts/verify_topology.py`，只是這裡的薄殼。把邏輯放進套件，
純判定（如 source IP 可分辨性）才能不碰網路單元測試。

這是環境斷言：真正的四區網段建置屬 workstream 6。缺環境時 CLI 明確失敗，
不 fake pass —— 一個永遠綠的拓樸檢查等於沒有檢查。
"""

from __future__ import annotations

import socket

#: 三條跨世代 port（SA §12.2）。
MGMT_PORTS = {3100: "Loki", 9090: "Prometheus", 4317: "OTLP"}
#: 契約 1 的 deny canary；range 在 Z-MGMT 刻意監聽它，確保失敗是 firewall 擋下。
MGMT_DENIED_PORT = 22
EXPECTED_KALI = 6


def reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def check_target_to_mgmt(mgmt: str) -> list[str]:
    """契約1：TARGET→MGMT 只准三個 telemetry port，其他 port 必須不通。"""
    fails = [
        f"契約1 破：TARGET→MGMT {name} :{port} 不通"
        for port, name in MGMT_PORTS.items()
        if not reachable(mgmt, port)
    ]
    if reachable(mgmt, MGMT_DENIED_PORT, timeout=2.0):
        fails.append(
            f"契約1 破：TARGET→MGMT 非 telemetry :{MGMT_DENIED_PORT} 竟然通了"
        )
    return fails


def check_mgmt_to_target_blocked(target: str) -> list[str]:
    """契約2：MGMT→TARGET 反向必須不通（agent pull 的整個理由）。"""
    reachable_ports = [p for p in MGMT_PORTS if reachable(target, p, timeout=2.0)]
    if reachable_ports:
        return [f"契約2 破：MGMT→TARGET 反向竟然通了 {reachable_ports} —— 單向已被破壞"]
    return []


def check_red_to_mgmt_denied(mgmt: str) -> list[str]:
    """契約3：RED→MGMT deny all。"""
    reachable_ports = [p for p in MGMT_PORTS if reachable(mgmt, p, timeout=2.0)]
    if reachable_ports:
        return [f"契約3 破：RED→MGMT 應 deny all，卻連得上 {reachable_ports}"]
    return []


def check_source_ips_distinguishable(source_ips: list[str]) -> list[str]:
    """六台 kali 應出現六個可分辨 source IP，不被 SNAT 塌成主機 IP。"""
    unique = set(source_ips)
    if len(unique) < EXPECTED_KALI:
        return [
            f"六台 kali 只出現 {len(unique)} 個可分辨 source IP（預期 {EXPECTED_KALI}）"
            f" —— 可能被 SNAT 塌成主機 IP：{sorted(unique)}"
        ]
    return []


# --- 四區網段模型（SA §12）：docker compose network 逼近隔離（票 #15）--------
#
# compose network 名 = 區名。這裡靠的是 Docker 的一條硬性質：**沒有共享 network
# 的兩個容器彼此不可達**（連 embedded DNS 都不解析對方的服務名）。
#
# 能強制的（本函式檢查）：
#   - mgmt 平面住戶都在 z-mgmt（SA §12.1：Z-MGMT 只承載觀測與評估）
#   - collector 在 target 側（契約 4）
#   - z-red 與 z-mgmt 不得被任何容器橋接 → 契約 3（RED→MGMT deny）結構上成立
# 不能強制的（**不**在此檢查，委派 #13 的真網段/防火牆）：
#   - 契約 2 的**方向**（TARGET→MGMT 單向）—— 共享 network 天生雙向
#   - 真 VLAN/macvlan、六台 kali 可分辨 source IP、真單向防火牆
Z_APP = "z-app"
Z_MGMT = "z-mgmt"
Z_TARGET = "z-target"
Z_RED = "z-red"

#: 觀測／評估平面住戶。
MGMT_RESIDENTS = frozenset(
    {"postgres", "loki", "prometheus", "grafana", "receiver", "evaluation-engine"}
)
#: collector 必須在 target 側（契約 4）。
TARGET_COLLECTORS = frozenset({"alloy"})


def check_zone_assignments(service_networks: dict[str, set[str]]) -> list[str]:
    """對 compose 的 service→networks 對映，驗證 Docker membership 能強制的區規則。

    不在此 compose 的服務會被跳過（同一份邏輯要能驗只起部分服務的子集）。
    """
    fails: list[str] = []

    for svc in MGMT_RESIDENTS:
        nets = service_networks.get(svc)
        if nets is not None and Z_MGMT not in nets:
            fails.append(
                f"區違規：mgmt 住戶 {svc} 不在 {Z_MGMT}（在 {sorted(nets)}）"
            )

    for svc in TARGET_COLLECTORS:
        nets = service_networks.get(svc)
        if nets is not None and Z_TARGET not in nets:
            fails.append(
                f"契約4 破：collector {svc} 不在 target 側 {Z_TARGET}（在 {sorted(nets)}）"
            )

    bridgers = sorted(
        s for s, nets in service_networks.items() if Z_RED in nets and Z_MGMT in nets
    )
    if bridgers:
        fails.append(
            f"契約3 破：{bridgers} 同時橋接 {Z_RED} 與 {Z_MGMT}，"
            f"RED→MGMT 不再結構隔離"
        )

    return fails


# --- Z-MGMT 住戶部署就緒（票 #16，「Z-MGMT 軟體完成」里程碑）------------------
#
# Loki 3.x 是 distroless（gcr.io/distroless/static:nonroot），無 shell/wget/curl，
# **結構上無法做容器內 Docker healthcheck**（2026-08-09 查證官方 Dockerfile）。
# 故 Docker healthcheck gate 的住戶集合不含 loki；loki 的就緒改由 smoke test
# 直接從 host 探 :3100/ready 補上 —— 這比 process-alive 更強，證明它真在服務查詢。
MGMT_HEALTHCHECKED = MGMT_RESIDENTS - {"loki"}


def unhealthy_residents(
    ps_rows: list[dict], *, expected: frozenset[str] | set[str]
) -> list[str]:
    """給 `docker compose ps --format json` 的 rows，回傳未就緒的 expected 住戶。

    就緒＝該住戶出現在 ps、State 為 running、且 Health 為 healthy。
    找不到（沒起來）、非 running、或 Health 空（沒宣告 healthcheck）都算未就緒。
    只看 expected 集合 —— 非住戶服務不影響判定。
    """
    by_service = {r.get("Service"): r for r in ps_rows}
    bad: list[str] = []
    for svc in sorted(expected):
        row = by_service.get(svc)
        if row is None:
            bad.append(f"{svc}（未出現在 compose ps —— 沒起來）")
        elif row.get("State") != "running":
            bad.append(f"{svc}（State={row.get('State')}）")
        elif row.get("Health") != "healthy":
            bad.append(f"{svc}（Health={row.get('Health') or '無 healthcheck'}）")
    return bad
