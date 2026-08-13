"""P1 four-field telemetry contract: source, destination, time, action result.

The mappings are deliberately explicit because app JSON and Falco JSON use
different producer-owned names.  Every expression is an aggregating LogQL
query; a source only passes when all four queries return a positive count.
Prometheus is excluded: its OTLP path carries sampled counters, not discrete
actions, so source/destination/result fields have no honest row-level meaning.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class FieldContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceFieldContract:
    name: str
    queries: dict[str, str]


NORMALIZED_FIELDS = frozenset({"source_ip", "destination", "time", "action_result"})

# Registry entries describe liveness sources, not necessarily discrete attack logs.
# Every non-action source must be explicit here so a new source cannot silently
# escape the P1 field decision.
NON_ACTION_SOURCE_EXCLUSIONS = {
    "alloy": "transport heartbeat; it proves delivery, not a discrete action",
    "response-agent": "control-plane pull heartbeat; no attack destination/result row",
    "prometheus": "sampled OTLP counter, not a discrete action log",
}


def validate_app_record(record: dict) -> None:
    producer_fields = set(APP_FIELDS.values())
    missing = sorted(producer_fields - record.keys())
    if missing:
        raise FieldContractError(f"app record missing producer fields: {', '.join(missing)}")


APP_FIELDS = {
    "source_ip": "source_ip",
    "destination": "path",
    "time": "ts",
    "action_result": "outcome",
}


def app_contract(app: str) -> SourceFieldContract:
    base = f'{{app={json.dumps(app)}}} | json'
    return SourceFieldContract(
        name=app,
        queries={
            normalized: f'sum(count_over_time({base} | {producer} != "" [10m]))'
            for normalized, producer in APP_FIELDS.items()
        },
    )


FALCO_FIELDS = {
    # SOURCE_IP is copied from the TCP peer into proc.cmdline by the target app.
    "source_ip": '| regexp `SOURCE_IP=(?P<source_ip>[0-9.]+)` | source_ip != ""',
    # Falco's affected object for command execution is the process command line.
    "destination": '| json | output_fields_proc_cmdline != ""',
    "time": '| json | time != ""',
    # A matched Falco rule is the sensor's discrete action result.
    "action_result": '| json | rule != ""',
}


def falco_contract() -> SourceFieldContract:
    base = '{job="falco"} |= `PurpleScope exec detected`'
    return SourceFieldContract(
        name="falco",
        queries={
            normalized: f"sum(count_over_time({base} {pipeline} [10m]))"
            for normalized, pipeline in FALCO_FIELDS.items()
        },
    )


def query_scalar(base_url: str, logql: str, timeout_s: float = 5.0) -> float:
    params = urllib.parse.urlencode({"query": logql})
    url = f"{base_url.rstrip('/')}/loki/api/v1/query?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload = json.load(response)
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise FieldContractError(f"Loki field query failed: {exc}") from exc
    results = (payload.get("data") or {}).get("result") or []
    return sum(float(item["value"][1]) for item in results if item.get("value"))


def verify_contract(
    contract: SourceFieldContract,
    base_url: str,
) -> dict[str, float]:
    counts = {field: query_scalar(base_url, query) for field, query in contract.queries.items()}
    missing = [field for field, count in counts.items() if count <= 0]
    if missing:
        raise FieldContractError(
            f"{contract.name}: four-field contract missing {', '.join(missing)}"
        )
    return counts
