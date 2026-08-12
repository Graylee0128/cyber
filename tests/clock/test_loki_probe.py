import json
from datetime import datetime, timezone

import pytest

from purple.clock.probes import ProbeError, parse_loki_delta


INGESTED = datetime(2026, 8, 12, 14, 0, 0, 250000, tzinfo=timezone.utc)


def payload(line: dict, *, ingested=INGESTED):
    return {
        "data": {
            "result": [
                {"values": [[str(int(ingested.timestamp() * 1e9)), json.dumps(line)]]}
            ]
        }
    }


def test_json_source_time_is_rebased_to_loki_ingestion_time():
    source = datetime(2026, 8, 12, 14, 0, 0, 100000, tzinfo=timezone.utc)
    observed = parse_loki_delta(payload({"ts": source.isoformat()}), "ts", INGESTED)
    assert observed == source


@pytest.mark.parametrize("bad", [{}, {"ts": "not-a-time"}, {"ts": "2026-08-12T14:00:00"}])
def test_missing_or_invalid_source_time_fails_loudly(bad):
    with pytest.raises(ProbeError):
        parse_loki_delta(payload(bad), "ts", INGESTED)


def test_empty_loki_result_is_not_a_green_clock_check():
    with pytest.raises(ProbeError, match="no matching log line"):
        parse_loki_delta({"data": {"result": []}}, "ts", INGESTED)
