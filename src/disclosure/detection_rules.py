"""偵測規則的註冊順序 —— `Detection #N` 標籤的唯一排序來源。

為什麼住在共用契約套件而不是各出口自己讀：兩個出口若各自決定順序，同一條規則
在 Evidence API 會是 `Detection #2`、在 SSE 會是 `Detection #3`，藍隊拿兩個畫面
對照時會以為那是兩條不同的規則。標籤要有用就必須全平台一致。

順序＝**檔案裡的出現順序**（註冊順序），不是字母序 —— 字母序會讓標籤順序本身
洩漏分類（`FalcoCommandExec` 永遠排在 `SQLInjectionBurst` 前面）。

讀設定用檔案是 WS7 spec §0.2 允許的（「讀設定可以用檔案，下命令必須是呼叫」）。
檔案讀不到時回空 tuple —— 於是標籤查不到、`rule` 欄位被整個拿掉。**少一個欄位，
不會多一個答案。**
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

log = logging.getLogger("disclosure.detection_rules")

#: Grafana provisioning 的規則檔。`PURPLE_DETECTION_RULES_PATH` 可覆寫。
DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "grafana"
    / "provisioning"
    / "alerting"
    / "rules.yaml"
)


def rules_path() -> Path:
    return Path(os.environ.get("PURPLE_DETECTION_RULES_PATH", DEFAULT_RULES_PATH))


def load_rule_titles(path: Path | str | None = None) -> tuple[str, ...]:
    """規則標題，依檔案出現順序、去重。讀不到就回空 tuple（fail closed）。"""
    target = Path(path) if path is not None else rules_path()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("偵測規則清單讀不到（%s）：%s —— rule 欄位將對紅藍完全遮蔽", target, exc)
        return ()

    if not isinstance(raw, dict):
        return ()

    titles: list[str] = []
    for group in raw.get("groups") or []:
        if not isinstance(group, dict):
            continue
        for rule in group.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            title = rule.get("title")
            if isinstance(title, str) and title and title not in titles:
                titles.append(title)
    return tuple(titles)
