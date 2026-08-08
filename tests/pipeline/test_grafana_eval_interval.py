"""Grafana eval interval = 10s（票 03、spec §1）—— 對 provisioning 檔的設定斷言。

環境未就位，但契約值可以先鎖。改這個值＝改 MTTD 地板。
"""

from pathlib import Path

import yaml

ALERTING = Path(__file__).resolve().parents[2] / "config" / "grafana" / "alerting.yaml"


def test_provisioning_file_exists():
    assert ALERTING.exists(), f"缺少 Grafana 告警 provisioning：{ALERTING}"


def test_every_group_evaluates_every_10s():
    doc = yaml.safe_load(ALERTING.read_text(encoding="utf-8"))
    groups = doc["groups"]
    assert groups, "provisioning 沒有任何 rule group"
    for group in groups:
        assert group["interval"] == "10s", f"group {group['name']} 的 interval 不是 10s"
