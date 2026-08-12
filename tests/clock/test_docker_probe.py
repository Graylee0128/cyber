from subprocess import CompletedProcess

from purple.clock.probes import read_docker


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
