"""`Detection #N` 的排序來源 —— 兩個出口共用同一份順序，標籤才對得起來。"""

from disclosure.detection_rules import DEFAULT_RULES_PATH, load_rule_titles

RULES_YAML = """
groups:
  - name: purple-detection
    rules:
      - title: SQLInjectionBurst
      - title: SSHBruteForce
      - title: SQLInjectionBurst
      - notitle: ignored
      - title: FalcoCommandExec
"""


def test_titles_follow_file_order_and_dedupe(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(RULES_YAML, encoding="utf-8")

    assert load_rule_titles(path) == (
        "SQLInjectionBurst",
        "SSHBruteForce",
        "FalcoCommandExec",
    )


def test_missing_file_is_empty_not_an_error(tmp_path):
    """讀不到 → 空 tuple → 標籤查不到 → rule 欄位被拿掉。fail closed。"""
    assert load_rule_titles(tmp_path / "nope.yaml") == ()


def test_malformed_file_is_empty(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("just a string", encoding="utf-8")
    assert load_rule_titles(path) == ()


def test_shipped_grafana_rules_are_loadable():
    """真的那份 provisioning 檔讀得出規則 —— 否則正式環境的標籤會全空。"""
    titles = load_rule_titles(DEFAULT_RULES_PATH)
    assert "SQLInjectionBurst" in titles
    assert "SSHBruteForce" in titles
