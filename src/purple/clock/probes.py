"""I/O shell —— 真的去讀某個節點的時間。

這裡是唯一碰 subprocess 的地方。判定與編排都在 `skew.py` / `runner.py`，
所以那兩層可以在毫秒內測完。
"""

from __future__ import annotations

import subprocess
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


def probe_for(node: NodeConfig) -> Callable[[], datetime]:
    if node.probe == "local":
        return read_local
    if node.probe == "docker":
        container = node.container
        if not container:
            # 設定解析已擋掉這種情況；直接建構 NodeConfig 才會走到這裡。
            raise ProbeError(f"node {node.name!r} uses the docker probe but names no container")
        return lambda: read_docker(container)
    raise ProbeError(f"no probe implementation for {node.probe!r}")
