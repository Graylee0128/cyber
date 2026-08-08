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
    """契約1：TARGET→MGMT 的三個 port 必須通。"""
    return [
        f"契約1 破：TARGET→MGMT {name} :{port} 不通"
        for port, name in MGMT_PORTS.items()
        if not reachable(mgmt, port)
    ]


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
