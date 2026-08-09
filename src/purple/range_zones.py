"""讀取 range 分區定址的唯一定義檔（`scripts/range/zones.env`）。

shell 端直接 `source` 那支檔案；Python 端（測試與日後的 Range Core）走這裡，
兩邊看到的是同一份位址。刻意不引 dotenv 之類的套件 —— 檔案格式只有
`KEY=value` 與 `#` 註解，自己解析五行就夠，少一個依賴。
"""

from __future__ import annotations

from pathlib import Path

ZONES_ENV = Path(__file__).resolve().parents[2] / "scripts" / "range" / "zones.env"


def load_zones(path: Path | str | None = None) -> dict[str, str]:
    """把 zones.env 解析成 dict。

    格式限制是刻意的：不支援變數展開、不支援引號、不支援行內註解 —— 因為 shell 端
    是直接 `source`，任何 Python 這邊「聰明」的解析都會讓兩邊語意分岔。
    """
    target = Path(path) if path is not None else ZONES_ENV
    zones: dict[str, str] = {}
    for lineno, raw in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{target}:{lineno} 不是 KEY=value：{raw!r}")
        key, _, value = line.partition("=")
        zones[key.strip()] = value.strip()
    return zones


def zone_cidr(zones: dict[str, str], vlan_key: str) -> str:
    """由 prefix 與 VLAN id 組出該區網段，例如 Z_TARGET_VLAN → 10.167.20.0/24。"""
    return f"{zones['RANGE_NET_PREFIX']}.{zones[vlan_key]}.0/24"


def red_ips(zones: dict[str, str]) -> list[str]:
    """六台紅隊的位址，由 RED_IP_FIRST 與 RED_COUNT 推出（與 attach-red.sh 同一規則）。"""
    prefix, _, last = zones["RED_IP_FIRST"].rpartition(".")
    first = int(last)
    return [f"{prefix}.{first + i}" for i in range(int(zones["RED_COUNT"]))]
