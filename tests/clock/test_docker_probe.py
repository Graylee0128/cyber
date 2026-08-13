import subprocess
from subprocess import CompletedProcess

import pytest

from purple.clock.probes import ProbeError, read_docker


def test_compose_service_name_resolves_to_runtime_container(monkeypatch):
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "exec", "alloy"]:
            return CompletedProcess(argv, 1, "", "No such container")
        if argv[:4] == ["docker", "compose", "ps", "-q"]:
            return CompletedProcess(argv, 0, "container-id\n", "")
        return CompletedProcess(argv, 0, "1786550038.5\n", "")

    monkeypatch.setattr("purple.clock.probes.subprocess.run", fake_run)
    observed = read_docker("alloy")
    assert observed.timestamp() == 1786550038.5
    assert ["docker", "exec", "container-id", "date", "+%s.%N"] in calls


def test_resolved_container_timeout_becomes_probe_error(monkeypatch):
    calls = 0

    def fake_run(argv, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return CompletedProcess(argv, 1, "", "No such container")
        if calls == 2:
            return CompletedProcess(argv, 0, "container-id\n", "")
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr("purple.clock.probes.subprocess.run", fake_run)
    with pytest.raises(ProbeError, match="timed out"):
        read_docker("alloy")
