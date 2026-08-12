"""I/O shell —— 真的去讀某個節點的時間。

這裡是唯一碰 subprocess 的地方。判定與編排都在 `skew.py` / `runner.py`，
所以那兩層可以在毫秒內測完。
"""

from __future__ import annotations

import subprocess
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from purple.clock.config import NodeConfig

#: `docker exec` 本身可能卡住。逾時比掛住好 —— 掛住的檢查等於沒有檢查。
PROBE_TIMEOUT_S = 10


class ProbeError(Exception):
    """讀不到節點時間。由 runner 轉成 UNREACHABLE，不會讓整批檢查中斷。"""


def read_local() -> datetime:
    return datetime.now(timezone.utc)


def read_docker(container: str, timeout_s: int = PROBE_TIMEOUT_S) -> datetime:
    """在容器內執行 `date` 取得它自己的時間。

    用 `%s.%N`（epoch 秒＋奈秒）而非 ISO 格式：BusyBox 的 date 不支援
    `-Ins`，而 epoch 格式在 BusyBox、coreutils、Alpine 上行為一致。
    """
    try:
        completed = subprocess.run(
            ["docker", "exec", container, "date", "+%s.%N"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise ProbeError("docker CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"docker exec timed out after {timeout_s}s") from exc

    if completed.returncode != 0:
        raise ProbeError(f"docker exec failed: {completed.stderr.strip()}")

    return parse_epoch(completed.stdout.strip(), container)


def parse_epoch(text: str, node: str) -> datetime:
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    except ValueError as exc:
        raise ProbeError(f"{node}: unparsable time output {text!r}") from exc


def parse_loki_delta(payload: dict, timestamp_field: str, ingested_at: datetime) -> datetime:
    """Return source time from Loki's newest line; Loki entry time is the reference.

    ``ingested_at`` is accepted explicitly so tests can prove the comparison without
    network access.  Missing or timezone-less source timestamps are failures, never
    silently treated as UTC.
    """
    results = (payload.get("data") or {}).get("result") or []
    newest: tuple[int, str] | None = None
    for stream in results:
        for stamp, line in stream.get("values", []):
            candidate = (int(stamp), line)
            if newest is None or candidate[0] > newest[0]:
                newest = candidate
    if newest is None:
        raise ProbeError("Loki returned no matching log line")
    try:
        source_text = json.loads(newest[1])[timestamp_field]
        source_time = datetime.fromisoformat(source_text.replace("Z", "+00:00"))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ProbeError(
            f"Loki line has no valid {timestamp_field!r} source timestamp"
        ) from exc
    if source_time.tzinfo is None or source_time.tzinfo.utcoffset(source_time) is None:
        raise ProbeError(f"Loki field {timestamp_field!r} has no timezone")
    if ingested_at.tzinfo is None or ingested_at.tzinfo.utcoffset(ingested_at) is None:
        raise ProbeError("Loki ingestion timestamp has no timezone")
    return source_time


def read_loki_ingest_delta(
    selector: str,
    timestamp_field: str,
    *,
    base_url: str | None = None,
    timeout_s: int = PROBE_TIMEOUT_S,
) -> datetime:
    """Read source time while making Loki's newest entry timestamp the host reference.

    ``runner.measure`` compares the returned source time to the current host clock.
    Shift it by ``host_now - Loki_ingest_time`` so the measured delta becomes exactly
    ``source_time - ingestion_time`` and does not require credentials inside the VM.
    """
    base = (base_url or os.environ.get("PURPLE_LOKI_URL", "http://localhost:3100")).rstrip("/")
    now = datetime.now(timezone.utc)
    params = urllib.parse.urlencode(
        {
            "query": selector,
            "start": str(int((now.timestamp() - 600) * 1e9)),
            "end": str(int(now.timestamp() * 1e9)),
            "limit": "20",
            "direction": "backward",
        }
    )
    url = f"{base}/loki/api/v1/query_range?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise ProbeError(f"Loki clock probe failed ({base}): {exc}") from exc

    results = (payload.get("data") or {}).get("result") or []
    stamps = [int(value[0]) for stream in results for value in stream.get("values", [])]
    if not stamps:
        raise ProbeError("Loki returned no matching log line")
    ingested = datetime.fromtimestamp(max(stamps) / 1e9, tz=timezone.utc)
    source = parse_loki_delta(payload, timestamp_field, ingested)
    return datetime.now(timezone.utc) + (source - ingested)


def probe_for(node: NodeConfig) -> Callable[[], datetime]:
    if node.probe == "local":
        return read_local
    if node.probe == "docker":
        container = node.container
        if not container:
            # 設定解析已擋掉這種情況；直接建構 NodeConfig 才會走到這裡。
            raise ProbeError(f"node {node.name!r} uses the docker probe but names no container")
        return lambda: read_docker(container)
    if node.probe == "loki_ingest_delta":
        if not node.selector or not node.timestamp_field:
            raise ProbeError(f"node {node.name!r} has incomplete Loki probe config")
        return lambda: read_loki_ingest_delta(node.selector, node.timestamp_field)
    raise ProbeError(f"no probe implementation for {node.probe!r}")
