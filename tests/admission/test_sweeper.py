import pytest

from admission.api import AdmissionSettings
from admission.sweeper import SweeperConfig, run


class ServiceFake:
    def __init__(self): self.calls = []
    def expire_requests(self, timeout):
        self.calls.append(timeout)
        return {"retry": 0, "released": 0}


def test_sweeper_config_requires_explicit_positive_values(monkeypatch):
    monkeypatch.delenv("ADMISSION_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("ADMISSION_SWEEP_INTERVAL_SECONDS", raising=False)
    with pytest.raises(ValueError):
        SweeperConfig.from_env()
    monkeypatch.setenv("ADMISSION_REQUEST_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("ADMISSION_SWEEP_INTERVAL_SECONDS", "0")
    with pytest.raises(ValueError):
        SweeperConfig.from_env()


def test_sweeper_one_shot_calls_timeout_once():
    fake = ServiceFake()
    run(SweeperConfig(timeout_seconds=10, interval_seconds=2, once=True), service_factory=lambda: fake)
    assert fake.calls == [10]


def test_sweeper_loop_uses_injected_wait_without_sleeping():
    fake, waits = ServiceFake(), []
    def wait(seconds):
        waits.append(seconds)
        return len(waits) == 2
    run(SweeperConfig(timeout_seconds=10, interval_seconds=3), service_factory=lambda: fake, wait=wait)
    assert fake.calls == [10, 10]
    assert waits == [3, 3]
