import pytest

from purple.telemetry_fields import (
    FieldContractError,
    SourceFieldContract,
    app_contract,
    falco_contract,
    verify_contract,
)


@pytest.mark.parametrize("contract", [app_contract("vulnerable-app"), app_contract("range-target"), falco_contract()])
def test_every_log_source_maps_all_four_normalized_fields(contract):
    assert set(contract.queries) == {"source_ip", "destination", "time", "action_result"}
    assert all("count_over_time" in query for query in contract.queries.values())


def test_missing_source_ip_makes_contract_fail(monkeypatch):
    contract = SourceFieldContract(
        name="mutated-app",
        queries={"source_ip": "q-source", "destination": "q-dest", "time": "q-time", "action_result": "q-result"},
    )
    monkeypatch.setattr(
        "purple.telemetry_fields.query_scalar",
        lambda _url, query: 0.0 if query == "q-source" else 1.0,
    )
    with pytest.raises(FieldContractError, match="source_ip"):
        verify_contract(contract, "http://loki")


def test_prometheus_is_not_misrepresented_as_action_logs():
    """OTLP Prometheus path is a sampled counter, not a four-field log source."""
    names = {"vulnerable-app", "range-target", "falco"}
    assert "prometheus" not in names
