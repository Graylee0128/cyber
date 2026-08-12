"""Response 封鎖來源必須由 Grafana Core Event label 一路帶入，不可猜測。"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy" / "grafana" / "provisioning" / "alerting" / "rules.yaml"


def test_falco_exec_alert_preserves_source_ip_as_a_series_label():
    document = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    [group] = document["groups"]
    rule = next(item for item in group["rules"] if item["uid"] == "falco-command-exec")
    query = next(item for item in rule["data"] if item["refId"] == "A")["model"]["expr"]

    assert "sum by (source_ip)" in query
    assert "(?P<source_ip>" in query


def test_sqli_alert_preserves_json_source_ip_as_a_series_label():
    document = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    [group] = document["groups"]
    rule = next(item for item in group["rules"] if item["uid"] == "sqli-injection-burst")
    query = next(item for item in rule["data"] if item["refId"] == "A")["model"]["expr"]

    assert "sum by (source_ip)" in query
