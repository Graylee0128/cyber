import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "scripts/range/contract1_probe.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("contract1_clock_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, date):
        self.date = date

    def read(self):
        return b"ready"

    def getheader(self, name):
        return self.date if name == "Date" else None


class Connection:
    def __init__(self, response):
        self.response = response

    def request(self, *_args):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        pass


def test_vm_clock_uses_request_midpoint_against_mgmt_date(monkeypatch):
    probe = load_probe()
    moments = iter(
        [
            datetime(2026, 8, 13, 1, 0, 0, 200000, tzinfo=timezone.utc),
            datetime(2026, 8, 13, 1, 0, 0, 400000, tzinfo=timezone.utc),
        ]
    )
    response = Response("Thu, 13 Aug 2026 01:00:00 GMT")
    monkeypatch.setattr(probe.http.client, "HTTPConnection", lambda *_a, **_k: Connection(response))
    assert probe.probe_mgmt_clock("mgmt-stub", now=lambda: next(moments)) == 300


def test_vm_clock_missing_independent_date_fails(monkeypatch):
    probe = load_probe()
    response = Response(None)
    monkeypatch.setattr(probe.http.client, "HTTPConnection", lambda *_a, **_k: Connection(response))
    with pytest.raises(RuntimeError, match="no Date"):
        probe.probe_mgmt_clock("mgmt-stub")
