"""節點設定解析 —— 純函數，不碰檔案系統以外的東西。

設定檔寫錯是「檢查永遠綠」最常見的來源，所以錯誤一律要吵，不可靜默略過。
"""

import pytest

from purple.clock.config import ClockConfig, ConfigError, parse_config

MINIMAL = {
    "reference": "mgmt-host",
    "nodes": [
        {"name": "mgmt-host", "probe": "local"},
        {"name": "alloy", "probe": "docker", "container": "alloy"},
    ],
}


class TestParsing:
    def test_parses_nodes_and_reference(self):
        cfg = parse_config(MINIMAL)
        assert isinstance(cfg, ClockConfig)
        assert cfg.reference == "mgmt-host"
        assert [n.name for n in cfg.nodes] == ["mgmt-host", "alloy"]

    def test_threshold_defaults_to_the_named_constant(self):
        assert parse_config(MINIMAL).threshold_ms == 100

    def test_threshold_can_be_overridden(self):
        assert parse_config({**MINIMAL, "threshold_ms": 50}).threshold_ms == 50

    def test_docker_node_keeps_its_container(self):
        alloy = parse_config(MINIMAL).nodes[1]
        assert alloy.probe == "docker"
        assert alloy.container == "alloy"

    def test_loki_delta_node_keeps_query_contract(self):
        raw = {
            "reference": "mgmt-host",
            "nodes": [
                {"name": "mgmt-host", "probe": "local"},
                {
                    "name": "target-app",
                    "probe": "loki_ingest_delta",
                    "selector": '{app="range-target"}',
                    "timestamp_field": "ts",
                },
            ],
        }
        node = parse_config(raw).nodes[1]
        assert node.selector == '{app="range-target"}'
        assert node.timestamp_field == "ts"


class TestBadConfigIsLoud:
    def test_empty_node_list_is_rejected(self):
        with pytest.raises(ConfigError, match="at least one node"):
            parse_config({"reference": "x", "nodes": []})

    def test_reference_must_be_one_of_the_nodes(self):
        bad = {**MINIMAL, "reference": "nowhere"}
        with pytest.raises(ConfigError, match="nowhere"):
            parse_config(bad)

    def test_unknown_probe_type_is_rejected(self):
        bad = {"reference": "a", "nodes": [{"name": "a", "probe": "telepathy"}]}
        with pytest.raises(ConfigError, match="telepathy"):
            parse_config(bad)

    def test_docker_node_without_container_is_rejected(self):
        bad = {"reference": "a", "nodes": [{"name": "a", "probe": "docker"}]}
        with pytest.raises(ConfigError, match="container"):
            parse_config(bad)

    @pytest.mark.parametrize("missing", ["selector", "timestamp_field"])
    def test_loki_delta_node_requires_complete_contract(self, missing):
        entry = {
            "name": "target-app",
            "probe": "loki_ingest_delta",
            "selector": '{app="range-target"}',
            "timestamp_field": "ts",
        }
        entry.pop(missing)
        bad = {
            "reference": "mgmt-host",
            "nodes": [{"name": "mgmt-host", "probe": "local"}, entry],
        }
        with pytest.raises(ConfigError, match=missing):
            parse_config(bad)

    def test_duplicate_node_names_are_rejected(self):
        """重名會讓報告指錯節點，違背「明確指出是哪個節點」。"""
        bad = {
            "reference": "a",
            "nodes": [{"name": "a", "probe": "local"}, {"name": "a", "probe": "local"}],
        }
        with pytest.raises(ConfigError, match="duplicate"):
            parse_config(bad)
