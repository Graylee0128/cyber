"""Issue #18 production seam: scenario definition + telemetry -> Registry."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from purple.metrics.gaps import MissClass, classify_from_registry
from purple.registry.production import (
    CatalogError,
    HeartbeatUnavailable,
    LokiHeartbeatReader,
    RegistryService,
    load_catalog,
)
from purple.registry.source_registry import SourceState


NOW = datetime(2026, 8, 11, 6, 0, 0, tzinfo=timezone.utc)


class FakeHeartbeatReader:
    """System-boundary fake: returns known telemetry timestamps by source id."""

    def __init__(self, beats):
        self.beats = beats
        self.queries: list[tuple[str, str]] = []

    def latest(self, source_id, query, now):
        self.queries.append((source_id, query))
        return self.beats.get(source_id)


class TestScenarioCatalog:
    def test_shipped_catalog_is_the_single_expected_source_definition(self):
        catalog = load_catalog()

        sources = catalog.scenario_sources("falco-exec-01")

        assert [(source.id, source.expected) for source in sources] == [
            ("falco", True),
            ("alloy", True),
            ("response-agent", True),
        ]
        assert catalog.signal("falco").query == '{job="falco"}'
        assert "alloy.heartbeat" in catalog.signal("alloy").query
        assert catalog.signal("response-agent").query == '{job="response-agent"}'

    def test_sources_not_expected_by_scenario_are_explicitly_absent(self):
        catalog = load_catalog()

        sources = catalog.scenario_sources("sqli-01")

        assert [(source.id, source.expected) for source in sources] == [
            ("falco", False),
            ("alloy", True),
            ("response-agent", True),
        ]

    def test_unknown_source_reference_is_rejected(self, tmp_path):
        path = tmp_path / "scenario-sources.yaml"
        path.write_text(
            """
sources:
  - id: falco
    heartbeat: {provider: loki, query: '{job="falco"}'}
scenarios:
  - id: bad
    expected_sources: [phantom]
""",
            encoding="utf-8",
        )

        with pytest.raises(CatalogError, match="phantom"):
            load_catalog(path)

    def test_duplicate_scenario_is_rejected(self, tmp_path):
        path = tmp_path / "scenario-sources.yaml"
        path.write_text(
            """
sources:
  - id: falco
    heartbeat: {provider: loki, query: '{job="falco"}'}
scenarios:
  - id: same
    expected_sources: [falco]
  - id: same
    expected_sources: [falco]
""",
            encoding="utf-8",
        )

        with pytest.raises(CatalogError, match="same"):
            load_catalog(path)


class TestRegistryService:
    def test_registry_uses_scenario_expected_list_and_collected_heartbeats(self):
        catalog = load_catalog()
        reader = FakeHeartbeatReader(
            {
                "falco": NOW - timedelta(seconds=10),
                "alloy": NOW - timedelta(seconds=20),
                "response-agent": NOW - timedelta(seconds=200),
            }
        )

        registry = RegistryService(catalog, reader, now=lambda: NOW).get("falco-exec-01")

        assert registry.get("falco").state is SourceState.HEALTHY
        assert registry.get("alloy").state is SourceState.HEALTHY
        assert registry.get("response-agent").state is SourceState.STALE
        assert {source for source, _query in reader.queries} == {
            "falco",
            "alloy",
            "response-agent",
        }

    def test_stopped_expected_source_stays_in_registry_as_stale(self):
        catalog = load_catalog()
        registry = RegistryService(
            catalog,
            FakeHeartbeatReader({"alloy": NOW}),
            now=lambda: NOW,
        ).get("falco-exec-01")

        falco = registry.get("falco")
        assert falco is not None
        assert falco.expected is True
        assert falco.state is SourceState.STALE
        assert falco.state is not SourceState.ABSENT

    def test_miss_classification_reads_state_from_registry(self):
        registry = RegistryService(
            load_catalog(),
            FakeHeartbeatReader({"alloy": NOW, "response-agent": NOW}),
            now=lambda: NOW,
        ).get("falco-exec-01")

        result = classify_from_registry(
            registry=registry,
            source_id="falco",
            detected=False,
            telemetry_present=False,
        )

        assert result is MissClass.UNKNOWN


class TestLokiHeartbeatReader:
    def test_returns_latest_loki_sample_timestamp(self):
        stamps = [
            str(int((NOW - timedelta(seconds=30)).timestamp() * 1e9)),
            str(int((NOW - timedelta(seconds=5)).timestamp() * 1e9)),
        ]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = {
                    "status": "success",
                    "data": {
                        "resultType": "streams",
                        "result": [{"stream": {"job": "falco"}, "values": [[stamps[0], "old"], [stamps[1], "new"]]}],
                    },
                }
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            reader = LokiHeartbeatReader(base_url=f"http://127.0.0.1:{server.server_port}")
            latest = reader.latest("falco", '{job="falco"}', NOW)
        finally:
            server.shutdown()
            thread.join()

        assert latest == NOW - timedelta(seconds=5)

    def test_backend_failure_is_not_silently_reported_as_stale(self):
        reader = LokiHeartbeatReader(base_url="http://127.0.0.1:1", timeout_s=0.1)

        with pytest.raises(HeartbeatUnavailable, match="Loki heartbeat"):
            reader.latest("falco", '{job="falco"}', NOW)
