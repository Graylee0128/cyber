"""`scripts/range/zones.env` 是分區定址的唯一定義處 —— 這支負責讓它保持唯一。

zones.env 刻意存純字面值（不做變數展開），好讓 shell 與 Python 都能零依賴解析。
代價是 gateway、push URL 這些衍生值看起來重複。這裡把那份一致性交給機器：漂掉就紅，
不靠人盯。另外兩件事也在這裡守：

- `config.alloy` 被烤進 golden image、用 Alloy 自己的語法，無法 source zones.env。
  它的 push URL 由本測試比對，不會默默漂移。
- range 的 shell 腳本不得再出現硬編位址（註解除外）—— 這正是 2026-08-09 code review
  抓到的 Shotgun Surgery：同一個位址散在六個檔，改一處漏五處。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from purple.range_zones import ZONES_ENV, load_zones, red_ips, zone_cidr

REPO = Path(__file__).resolve().parents[2]
RANGE_DIR = REPO / "scripts" / "range"


@pytest.fixture(scope="module")
def zones() -> dict[str, str]:
    return load_zones()


class TestInternalConsistency:
    """衍生值必須與 prefix / VLAN id 對得起來。"""

    @pytest.mark.parametrize(
        "gw_key,vlan_key",
        [
            ("Z_MGMT_GW", "Z_MGMT_VLAN"),
            ("Z_TARGET_GW", "Z_TARGET_VLAN"),
            ("Z_RED_GW", "Z_RED_VLAN"),
            ("Z_APP_GW", "Z_APP_VLAN"),
        ],
    )
    def test_gateway_matches_its_vlan(self, zones, gw_key, vlan_key):
        expected = f"{zones['RANGE_NET_PREFIX']}.{zones[vlan_key]}.1"
        assert zones[gw_key] == expected

    @pytest.mark.parametrize(
        "host_key,vlan_key",
        [
            ("MGMT_STUB_IP", "Z_MGMT_VLAN"),
            ("MGMT_LOKI_IP", "Z_MGMT_VLAN"),
            ("TARGET_IP", "Z_TARGET_VLAN"),
            ("RED_IP_FIRST", "Z_RED_VLAN"),
        ],
    )
    def test_host_sits_in_its_own_zone(self, zones, host_key, vlan_key):
        prefix = zone_cidr(zones, vlan_key).removesuffix(".0/24")
        assert zones[host_key].startswith(f"{prefix}."), (
            f"{host_key}={zones[host_key]} 不在 {vlan_key} 的網段內"
        )

    def test_loki_push_url_points_at_the_declared_loki(self, zones):
        assert zones["LOKI_PUSH_URL"] == (
            f"http://{zones['MGMT_LOKI_IP']}:3100/loki/api/v1/push"
        )

    def test_six_red_ips_are_distinct(self, zones):
        """六個可分辨 source IP 是防 G0 塌縮的硬需求（SA §12.1）—— 位址本身先不能重複。"""
        ips = red_ips(zones)
        assert len(ips) == int(zones["RED_COUNT"]) == 6
        assert len(set(ips)) == len(ips)


class TestNoDrift:
    def test_config_alloy_pushes_to_the_declared_loki(self, zones):
        """config.alloy 無法 source zones.env（Alloy 語法 + 被烤進 image），只能比對。"""
        text = (REPO / "deploy" / "range-target" / "config.alloy").read_text(encoding="utf-8")
        urls = re.findall(r'url\s*=\s*"([^"]+)"', text)
        assert urls, "config.alloy 找不到任何 loki.write endpoint url"
        assert urls == [zones["LOKI_PUSH_URL"]], (
            f"config.alloy 的 push URL {urls} 與 zones.env 的 "
            f"{zones['LOKI_PUSH_URL']} 不符 —— 位址漂了"
        )

    def test_range_scripts_hardcode_no_addresses(self, zones):
        """range 腳本的**程式行**不得再出現硬編位址；註解可以（說明用）。"""
        prefix = re.escape(zones["RANGE_NET_PREFIX"])
        offenders: list[str] = []
        for script in sorted(RANGE_DIR.glob("*.sh")):
            for lineno, raw in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
                code = raw.split("#", 1)[0]
                if re.search(rf"{prefix}\.\d", code):
                    offenders.append(f"{script.name}:{lineno}: {raw.strip()}")
        assert not offenders, (
            "這些程式行硬編了位址，請改用 zones.env 的變數：\n" + "\n".join(offenders)
        )

    def test_every_range_script_that_needs_zones_sources_it(self, zones):
        """會用到 zones.env 變數的腳本必須真的 source 它，否則 set -u 下會炸在半路。"""
        keys = [k for k in zones if k not in {"RANGE_NET_PREFIX"}]
        missing: list[str] = []
        for script in sorted(RANGE_DIR.glob("*.sh")):
            text = script.read_text(encoding="utf-8")
            uses = any(re.search(rf"\${{?{k}\b", text) for k in keys)
            if uses and "zones.env" not in text:
                missing.append(script.name)
        assert not missing, f"這些腳本用了 zones.env 的變數卻沒有 source：{missing}"


def test_zones_env_is_where_we_think_it_is():
    assert ZONES_ENV == RANGE_DIR / "zones.env"
    assert ZONES_ENV.exists()
