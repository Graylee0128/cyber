"""Source Registry 的 production I/O shell（Issue #18）。

expected 與 heartbeat 刻意分開：前者只讀 scenario 定義，後者只讀既有 Loki
telemetry。這層把兩份輸入交給純函式 ``evaluate_registry``，並提供 P2 可直接呼叫的
``registry_for_scenario(scenario_id)``；不為一個尚不存在的 HTTP API 引入 web framework。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

import yaml

from purple.registry.source_registry import Registry, ScenarioSource, evaluate_registry


DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "scenario-sources.yaml"


class CatalogError(ValueError):
    """Scenario source 定義不完整或互相矛盾。"""


class HeartbeatUnavailable(RuntimeError):
    """Loki 無法提供 heartbeat；不可把後端故障靜默洗成來源 stale。"""


@dataclass(frozen=True)
class HeartbeatSignal:
    id: str
    provider: str
    query: str


@dataclass(frozen=True)
class ScenarioDefinition:
    id: str
    expected_sources: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioCatalog:
    """`sources:` 是平台級 heartbeat 定義；`scenarios:` 與 `fixtures:` 語意分開
    （#43）——前者是真 scenario（期望清單改由 `scenarios/<id>/metadata.yaml`
    提供，這裡目前恆為空），後者是驗證 pipeline／registry 機制用的測試載具。
    兩者絕不合併成同一份清單：按 scenario 迭代時 fixture 不會被算進去。
    """

    signals: tuple[HeartbeatSignal, ...]
    scenarios: tuple[ScenarioDefinition, ...]
    fixtures: tuple[ScenarioDefinition, ...] = ()

    def signal(self, source_id: str) -> HeartbeatSignal:
        for signal in self.signals:
            if signal.id == source_id:
                return signal
        raise CatalogError(f"未知 source {source_id!r}")

    def scenario_sources(self, scenario_id: str) -> tuple[ScenarioSource, ...]:
        """僅在 `scenarios:`（真 scenario）裡查。"""
        return self._expected_sources(scenario_id, self.scenarios, "scenario", self.fixtures, "fixture")

    def fixture_sources(self, fixture_id: str) -> tuple[ScenarioSource, ...]:
        """僅在 `fixtures:`（測試載具）裡查。"""
        return self._expected_sources(fixture_id, self.fixtures, "fixture", self.scenarios, "scenario")

    def _expected_sources(
        self,
        item_id: str,
        items: tuple[ScenarioDefinition, ...],
        label: str,
        other_items: tuple[ScenarioDefinition, ...],
        other_label: str,
    ) -> tuple[ScenarioSource, ...]:
        definition = next((item for item in items if item.id == item_id), None)
        if definition is None:
            if any(item.id == item_id for item in other_items):
                raise CatalogError(
                    f"{item_id!r} 是 {other_label}，不是 {label}；改用 "
                    f"{'fixture_sources' if other_label == 'fixture' else 'scenario_sources'}()"
                )
            raise CatalogError(f"未知 {label} {item_id!r}")
        expected = set(definition.expected_sources)
        # 輸出走訪 catalog 的固定來源順序；未被列為 expected 的來源明確為 absent。
        return tuple(ScenarioSource(signal.id, signal.id in expected) for signal in self.signals)


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{label} 必須是非空字串")
    return value.strip()


def _items(raw: object, label: str) -> list[Mapping]:
    if not isinstance(raw, list) or not raw:
        raise CatalogError(f"{label} 必須是非空清單")
    if not all(isinstance(item, Mapping) for item in raw):
        raise CatalogError(f"{label} 每一筆必須是 mapping")
    return list(raw)


def _optional_items(raw: object, label: str) -> list[Mapping]:
    """同 `_items`，但允許空清單／缺席鍵 —— `scenarios:` 目前恆為空（#43）。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CatalogError(f"{label} 必須是清單")
    if not all(isinstance(item, Mapping) for item in raw):
        raise CatalogError(f"{label} 每一筆必須是 mapping")
    return list(raw)


def load_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> ScenarioCatalog:
    """載入並完整驗證 scenario expected sources 的單一 YAML。"""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"scenario source 定義讀取失敗：{exc}") from exc
    if not isinstance(raw, Mapping):
        raise CatalogError("scenario source 根節點必須是 mapping")

    signals: list[HeartbeatSignal] = []
    source_ids: set[str] = set()
    for index, item in enumerate(_items(raw.get("sources"), "sources")):
        source_id = _nonempty(item.get("id"), f"sources[{index}].id")
        if source_id in source_ids:
            raise CatalogError(f"重複 source {source_id!r}")
        source_ids.add(source_id)
        heartbeat = item.get("heartbeat")
        if not isinstance(heartbeat, Mapping):
            raise CatalogError(f"source {source_id!r} 缺 heartbeat mapping")
        provider = _nonempty(heartbeat.get("provider"), f"source {source_id!r} provider")
        if provider != "loki":
            raise CatalogError(f"source {source_id!r} provider {provider!r} 不支援")
        query = _nonempty(heartbeat.get("query"), f"source {source_id!r} query")
        signals.append(HeartbeatSignal(source_id, provider, query))

    scenarios = _parse_definitions(raw.get("scenarios"), "scenarios", "scenario", source_ids)
    fixtures = _parse_definitions(raw.get("fixtures"), "fixtures", "fixture", source_ids)

    return ScenarioCatalog(tuple(signals), tuple(scenarios), tuple(fixtures))


def _parse_definitions(
    raw: object, block_label: str, item_label: str, source_ids: set[str]
) -> list[ScenarioDefinition]:
    """解析 `scenarios:` 或 `fixtures:` 其中一個區塊；兩者結構相同但語意分開（#43）。"""
    definitions: list[ScenarioDefinition] = []
    ids: set[str] = set()
    for index, item in enumerate(_optional_items(raw, block_label)):
        item_id = _nonempty(item.get("id"), f"{block_label}[{index}].id")
        if item_id in ids:
            raise CatalogError(f"重複 {item_label} {item_id!r}")
        ids.add(item_id)
        expected_raw = item.get("expected_sources")
        if not isinstance(expected_raw, list):
            raise CatalogError(f"{item_label} {item_id!r} expected_sources 必須是清單")
        expected = tuple(_nonempty(source, f"{item_label} {item_id!r} source") for source in expected_raw)
        if len(expected) != len(set(expected)):
            raise CatalogError(f"{item_label} {item_id!r} expected_sources 有重複")
        unknown = set(expected) - source_ids
        if unknown:
            raise CatalogError(f"{item_label} {item_id!r} 引用未知 source {sorted(unknown)!r}")
        definitions.append(ScenarioDefinition(item_id, expected))
    return definitions


class HeartbeatReader(Protocol):
    def latest(self, source_id: str, query: str, now: datetime) -> datetime | None: ...


@dataclass(frozen=True)
class LokiHeartbeatReader:
    """以 LogQL 取每個獨立 heartbeat stream 的最後時間戳。"""

    base_url: str = "http://loki:3100"
    timeout_s: float = 5.0
    lookback_s: float = 86_400

    def latest(self, source_id: str, query: str, now: datetime) -> datetime | None:
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise ValueError("now 缺少 timezone")
        params = urllib.parse.urlencode(
            {
                "query": query,
                "start": str(int((now - timedelta(seconds=self.lookback_s)).timestamp() * 1e9)),
                "end": str(int(now.timestamp() * 1e9)),
                "limit": "1",
                "direction": "backward",
            }
        )
        url = f"{self.base_url.rstrip('/')}/loki/api/v1/query_range?{params}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as response:
                payload = json.loads(response.read())
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise HeartbeatUnavailable(f"{source_id}: Loki heartbeat 查詢失敗：{exc}") from exc
        if payload.get("status") != "success":
            raise HeartbeatUnavailable(f"{source_id}: Loki heartbeat 回傳非 success")

        stamps: list[int] = []
        for stream in (payload.get("data") or {}).get("result") or []:
            for value in stream.get("values") or []:
                if isinstance(value, (list, tuple)) and value:
                    try:
                        stamps.append(int(value[0]))
                    except (TypeError, ValueError):
                        raise HeartbeatUnavailable(f"{source_id}: Loki heartbeat timestamp 不合法")
        if not stamps:
            return None
        return datetime.fromtimestamp(max(stamps) / 1e9, tz=timezone.utc)


@dataclass(frozen=True)
class RegistryService:
    catalog: ScenarioCatalog
    heartbeats: HeartbeatReader
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def get(self, scenario_id: str) -> Registry:
        """P2／WS7 可直接呼叫的查詢 seam：scenario_id -> Registry。僅查真 scenario。"""
        return self._evaluate(scenario_id, self.catalog.scenario_sources)

    def get_fixture(self, fixture_id: str) -> Registry:
        """同 `get`，但查 `fixtures:`——測試載具走這條，不混進 scenario 查詢（#43）。"""
        return self._evaluate(fixture_id, self.catalog.fixture_sources)

    def _evaluate(
        self, item_id: str, lookup: Callable[[str], tuple[ScenarioSource, ...]]
    ) -> Registry:
        observed_at = self.now()
        declarations = lookup(item_id)
        collected: dict[str, datetime] = {}
        for declaration in declarations:
            if not declaration.expected:
                continue
            signal = self.catalog.signal(declaration.id)
            latest = self.heartbeats.latest(signal.id, signal.query, observed_at)
            if latest is not None:
                collected[signal.id] = latest
        return evaluate_registry(item_id, declarations, collected, observed_at)


def registry_for_scenario(
    scenario_id: str,
    *,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
    loki_url: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Registry:
    """Production convenience API；刻意是 Python 函式而不是新 HTTP server。僅查真 scenario。"""
    catalog = load_catalog(catalog_path)
    reader = LokiHeartbeatReader(base_url=loki_url or os.environ.get("PURPLE_LOKI_URL", "http://loki:3100"))
    return RegistryService(catalog, reader, now=now).get(scenario_id)


def registry_for_fixture(
    fixture_id: str,
    *,
    catalog_path: Path | str = DEFAULT_CATALOG_PATH,
    loki_url: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Registry:
    """同 `registry_for_scenario`，但查 `fixtures:`——供 pipeline／registry 機制驗證用。"""
    catalog = load_catalog(catalog_path)
    reader = LokiHeartbeatReader(base_url=loki_url or os.environ.get("PURPLE_LOKI_URL", "http://loki:3100"))
    return RegistryService(catalog, reader, now=now).get_fixture(fixture_id)
