"""查 Loki：某 selector 在最近時間窗內（可選含 needle）的行數。integration 用。

`telemetry_present` 的**真實訊號來源** —— 票 #9 的決定性測試靠它把
`classify_miss` 的 `telemetry_present` 接到真 Loki 查詢，而不是餵字面值：
規則沒開火（detected=False）但 Loki 裡確實有 Falco 那筆 → 判偵測缺口而非可見性缺口。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request


def loki_line_count(
    base_url: str,
    selector: str,
    needle: str | None = None,
    since_s: float = 300.0,
    timeout_s: float = 5.0,
) -> int:
    """回傳 `selector`（可加 `|= needle`）在最近 `since_s` 秒窗內的行數。"""
    end = time.time()
    start = end - since_s
    logql = f"{selector} |= `{needle}`" if needle else selector
    params = urllib.parse.urlencode(
        {
            "query": logql,
            "start": str(int(start * 1e9)),  # ns epoch
            "end": str(int(end * 1e9)),
            "limit": "1000",
            "direction": "backward",
        }
    )
    url = f"{base_url.rstrip('/')}/loki/api/v1/query_range?{params}"
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        payload = json.loads(resp.read())
    total = 0
    for stream in (payload.get("data") or {}).get("result") or []:
        total += len(stream.get("values", []))
    return total


def loki_has(
    base_url: str,
    selector: str,
    needle: str | None = None,
    since_s: float = 300.0,
    timeout_s: float = 5.0,
) -> bool:
    """selector（可加 needle）在窗內是否至少有一行。"""
    return loki_line_count(base_url, selector, needle, since_s, timeout_s) > 0
