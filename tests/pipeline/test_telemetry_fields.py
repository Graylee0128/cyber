import importlib.util
from pathlib import Path
import pytest
import yaml

from purple.telemetry_fields import (
    FieldContractError,
    NON_ACTION_SOURCE_EXCLUSIONS,
    NORMALIZED_FIELDS,
    app_contract,
    falco_contract,
    validate_app_record,
)

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def emitted_record(module, path):
    records = []
    handler = object.__new__(module.Handler)
    handler.path = path
    handler.client_address = ("10.167.30.11", 12345)
    handler.headers = {}
    handler._text = lambda *_args: None
    module._write_log = records.append
    handler.do_GET()
    return records[-1]


@pytest.mark.parametrize("contract", [app_contract("vulnerable-app"), app_contract("range-target"), falco_contract()])
def test_every_log_source_maps_all_four_normalized_fields(contract):
    assert set(contract.queries) == NORMALIZED_FIELDS
    assert all("count_over_time" in query for query in contract.queries.values())


@pytest.mark.parametrize(
    ("name", "path", "request_path"),
    [
        ("vulnerable", "deploy/vulnerable-app/app.py", "/login?username=test"),
        ("range_target", "deploy/range-target/app.py", "/probe"),
    ],
)
def test_real_app_producers_emit_all_four_fields(name, path, request_path):
    record = emitted_record(load_module(name, path), request_path)
    validate_app_record(record)


def test_removing_real_producer_source_ip_makes_contract_fail():
    module = load_module("mutated_range_target", "deploy/range-target/app.py")
    record = emitted_record(module, "/probe")
    record.pop("source_ip")
    with pytest.raises(FieldContractError, match="source_ip"):
        validate_app_record(record)


def test_prometheus_is_not_misrepresented_as_action_logs():
    """OTLP Prometheus path is a sampled counter, not a four-field log source."""
    names = {"vulnerable-app", "range-target", "falco"}
    assert "prometheus" not in names


def test_every_registered_liveness_source_is_covered_or_explicitly_excluded():
    catalog = yaml.safe_load((ROOT / "config/scenario-sources.yaml").read_text(encoding="utf-8"))
    registered = {item["id"] for item in catalog["sources"]}
    action_contracts = {"falco"}
    assert registered <= action_contracts | NON_ACTION_SOURCE_EXCLUSIONS.keys()
    assert all(NON_ACTION_SOURCE_EXCLUSIONS.values())


def test_stale_falco_counts_cannot_satisfy_fresh_action(monkeypatch):
    verifier = load_module("verify_p1_fields", "scripts/range/verify-p1-fields.py")
    baseline = {field: 3 for field in NORMALIZED_FIELDS}
    monkeypatch.setattr(verifier, "read_counts", lambda *_args: baseline.copy())
    with pytest.raises(FieldContractError, match="fresh action did not increase"):
        verifier.wait_for_increase(falco_contract(), "http://loki", baseline, 0)
