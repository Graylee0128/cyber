"""CH2 Foothold（校園海報上傳，#153 Campaign Pack v1）新增的兩條 Falco rule 結構測試。

同 `test_falco_rules.py` 的性質分級：真環境驗證（syscall 真的被 Falco 抓到）留給
大主機 golden VM（T4）；這裡只鎖住規則檔本身的結構不被誤改——condition 少了
evt.type、路徑打錯、tags 漏掉 technique 這類會讓規則悄悄失效但 CI 完全看不出來的
改動。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy" / "falco" / "rules.d" / "purplescope.yaml"


def _load_rules() -> list[dict]:
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def test_webshell_exec_rule_matches_python3_child_from_posters_dir():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope WebShell Exec")

    assert "evt.type=execve" in rule["condition"]
    assert "proc.name=python3" in rule["condition"]
    # 抓「從上傳目錄執行」這個行為本身，不是特定 payload 內容 —— 才不會被繞過。
    assert "/var/lib/purplescope/posters/" in rule["condition"]
    assert rule["priority"] == "WARNING"
    assert "T1505" in rule["tags"]


def test_webshell_exec_rule_output_includes_cmdline_and_parent():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope WebShell Exec")

    assert "%proc.cmdline" in rule["output"]
    assert "%proc.pname" in rule["output"]


def test_poster_sudo_find_abuse_rule_matches_the_gtfobins_pattern():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope Poster Sudo Find Abuse")

    assert "evt.type=execve" in rule["condition"]
    assert "proc.name=sudo" in rule["condition"]
    assert "find /var/lib/purplescope/posters" in rule["condition"]
    assert rule["priority"] == "WARNING"
    assert "T1548" in rule["tags"]


def test_ch2_rules_are_distinct_from_ch1_and_fixture_rules():
    """兩條新規則的 condition 不該意外疊到既有規則（例如都寫 proc.name=python3
    卻沒有路徑限定，會連 /exec /uncovered 的行為都一起誤觸）。"""
    rules = _load_rules()
    by_name = {r["rule"]: r for r in rules}

    webshell = by_name["PurpleScope WebShell Exec"]
    command_exec = by_name["PurpleScope Command Exec"]
    uncovered = by_name["PurpleScope Uncovered Action"]

    # /exec 與 /uncovered 走的是 sh/bash 系列＋PURPLESCOPE_EXEC/UNCOVERED marker，
    # 與 webshell 的 python3＋posters 路徑條件在語意上不重疊。
    assert "PURPLESCOPE_EXEC" in command_exec["condition"]
    assert "PURPLESCOPE_UNCOVERED" in uncovered["condition"]
    assert "PURPLESCOPE_EXEC" not in webshell["condition"]
    assert "PURPLESCOPE_UNCOVERED" not in webshell["condition"]
