"""`clock-check` —— 可重複執行的時鐘同步檢查。

    python -m purple.clock.cli [--config config/clock-nodes.yaml] [--json]

退出碼 0 = 全部節點在門檻內；非 0 = 有節點偏差、讀不到、量測不可信，
或設定檔沒有可檢查的節點。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from purple.clock.config import ConfigError, load_config
from purple.clock.runner import check
from purple.clock.skew import SkewReport

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG_ERROR = 2

DEFAULT_CONFIG = Path("config/clock-nodes.yaml")


def render(report: SkewReport, as_json: bool = False) -> tuple[str, int]:
    """報告 → (要印的文字, 退出碼)。純函數，方便測退出碼對應。"""
    if as_json:
        text = json.dumps(
            {
                "ok": report.ok,
                "threshold_ms": report.threshold_ms,
                "nodes": [
                    {
                        "node": r.node,
                        "verdict": str(r.verdict),
                        "skew_ms": r.skew_ms,
                        "detail": r.detail,
                    }
                    for r in report.results
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    else:
        lines = [report.summary()]
        if report.ok:
            lines += [f"  - {r.node}: {r.detail}" for r in report.results]
        text = "\n".join(lines)

    return text, EXIT_OK if report.ok else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clock-check", description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    text, code = render(check(config), as_json=args.json)
    print(text, file=sys.stdout if code == EXIT_OK else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
