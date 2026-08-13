"""節點設定的解析與驗證 —— 純函數，讀檔的部分只有一個薄殼。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from purple.clock.skew import MAX_SKEW_MS

PROBE_TYPES = ("local", "docker")


class ConfigError(Exception):
    """設定檔有問題。一律拋出，不可靜默略過 —— 略過的節點會渲染成綠燈。"""


@dataclass(frozen=True)
class NodeConfig:
    name: str
    probe: str
    container: str | None = None


@dataclass(frozen=True)
class ClockConfig:
    reference: str
    nodes: tuple[NodeConfig, ...]
    threshold_ms: float = MAX_SKEW_MS


def parse_config(raw: dict[str, Any]) -> ClockConfig:
    raw_nodes = raw.get("nodes") or []
    if not raw_nodes:
        raise ConfigError("config must list at least one node")

    nodes: list[NodeConfig] = []
    seen: set[str] = set()
    for entry in raw_nodes:
        name = entry.get("name")
        if not name:
            raise ConfigError("every node needs a name")
        if name in seen:
            raise ConfigError(f"duplicate node name {name!r} —— 報告會指錯節點")
        seen.add(name)

        probe = entry.get("probe")
        if probe not in PROBE_TYPES:
            raise ConfigError(f"unknown probe {probe!r}, expected one of {PROBE_TYPES}")

        container = entry.get("container")
        if probe == "docker" and not container:
            raise ConfigError(f"node {name!r} uses the docker probe but names no container")

        nodes.append(
            NodeConfig(
                name=name,
                probe=probe,
                container=container,
            )
        )

    reference = raw.get("reference")
    if reference not in seen:
        raise ConfigError(f"reference {reference!r} is not one of the configured nodes")

    return ClockConfig(
        reference=reference,
        nodes=tuple(nodes),
        threshold_ms=float(raw.get("threshold_ms", MAX_SKEW_MS)),
    )


def load_config(path: Path) -> ClockConfig:
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    return parse_config(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
