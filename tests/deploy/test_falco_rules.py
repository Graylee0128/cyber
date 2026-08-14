"""#62 結構測試：Z-BLUE seat sudo 偵測規則必須存在、比對 seat-blue-* 前綴。

真環境驗證（host Falco 實際吃到 seat-blue-* 容器的 sudo execve，container.name
正確解析非 <NA>）已在 10.167.223.45 上做過，見 PR 說明；這裡只鎖住規則檔本身的
結構不被誤改（e.g. 前綴打錯、condition 漏掉 evt.type）。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy" / "falco" / "rules.d" / "purplescope.yaml"


def _load_rules() -> list[dict]:
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))


def test_blue_seat_sudo_rule_exists_and_matches_seat_prefix():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope Blue Seat Sudo Attempt")

    assert "evt.type=execve" in rule["condition"]
    assert "proc.name=sudo" in rule["condition"]
    # 用 name 前綴而非 id：id 每次重建都變，name 遵守 BLUE_CONTAINER_PREFIX
    assert 'container.name startswith "seat-blue-"' in rule["condition"]
    assert rule["priority"] == "WARNING"
    assert "T1548" in rule["tags"]


def test_blue_seat_sudo_rule_output_includes_container_name():
    rules = _load_rules()
    rule = next(r for r in rules if r["rule"] == "PurpleScope Blue Seat Sudo Attempt")

    # container.id 在多容器環境下無法回推是哪個 seat，output 必須帶 container.name
    assert "%container.name" in rule["output"]
