"""CH4 Ghost in the System（校園報修診斷工具，#153 Campaign Pack v1）新增的
Falco rule 結構測試。同 `test_falco_rules.py` 的性質分級：真環境驗證（syscall
真的被 Falco 抓到）留給大主機 golden VM（T4）；這裡只鎖住規則檔本身的結構。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy" / "falco" / "rules.d" / "purplescope.yaml"


def _load_rules() -> list[dict]:
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def test_cron_persistence_write_rule_watches_the_fixed_cron_path():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope Cron Persistence Write")

    assert "evt.type in (open, openat, openat2)" in rule["condition"]
    assert "/etc/cron.d/campus-report" in rule["condition"]
    assert rule["priority"] == "WARNING"
    assert "T1053" in rule["tags"]


def test_cron_persistence_write_rule_output_includes_file_and_cmdline():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope Cron Persistence Write")

    assert "%fd.name" in rule["output"]
    assert "%proc.cmdline" in rule["output"]


def test_cron_persistence_rule_mirrors_the_proven_sensitive_file_pattern():
    """這條規則的寫法刻意複用 `PurpleScope Sensitive File Access`（同樣是「固定
    路徑 open 就算數」，不是新發明的偵測邏輯）——降低沒有真 Falco 環境可測時的風險。"""
    rules = _load_rules()
    by_name = {r["rule"]: r for r in rules}
    cron_rule = by_name["PurpleScope Cron Persistence Write"]
    sensitive_rule = by_name["PurpleScope Sensitive File Access"]

    assert "evt.type in (open, openat, openat2)" in sensitive_rule["condition"]
    # YAML `>` 折疊區塊已把原始換行變成空格，兩條規則的條件式在同一行裡；
    # 比對「evt.type in (...)」這段前綴仍然完全一致，只有路徑不同。
    assert cron_rule["condition"].split(" and ")[0].strip() == \
        sensitive_rule["condition"].split(" and ")[0].strip()
