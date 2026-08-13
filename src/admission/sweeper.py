from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Event
from typing import Callable

from admission.api import AdmissionSettings
from admission.range_client import HttpRangePublisher
from admission.service import AdmissionService, NullRangePublisher
from admission.store.db import connect, ensure_schema


@dataclass(frozen=True)
class SweeperConfig:
    timeout_seconds: int
    interval_seconds: int
    once: bool = False

    @classmethod
    def from_env(cls) -> "SweeperConfig":
        timeout = os.environ.get("ADMISSION_REQUEST_TIMEOUT_SECONDS")
        interval = os.environ.get("ADMISSION_SWEEP_INTERVAL_SECONDS")
        if not timeout or not interval:
            raise ValueError(
                "ADMISSION_REQUEST_TIMEOUT_SECONDS and ADMISSION_SWEEP_INTERVAL_SECONDS are required"
            )
        parsed_timeout, parsed_interval = int(timeout), int(interval)
        if parsed_timeout <= 0 or parsed_interval <= 0:
            raise ValueError("Admission sweep timeout and interval must be positive")
        return cls(
            timeout_seconds=parsed_timeout,
            interval_seconds=parsed_interval,
            once=os.environ.get("ADMISSION_SWEEP_ONCE") == "1",
        )


def _production_sweep(timeout_seconds: int) -> None:
    settings = AdmissionSettings.from_env()
    if settings.range_core_url and settings.range_core_token:
        publisher = HttpRangePublisher(settings.range_core_url, settings.range_core_token)
    elif settings.disable_range_publication:
        publisher = NullRangePublisher()
    else:
        raise ValueError(
            "ADMISSION_RANGE_CORE_URL and ADMISSION_RANGE_CORE_TOKEN are required"
        )
    conn = connect()
    try:
        ensure_schema(conn)
        AdmissionService(conn, publisher=publisher).expire_requested(timeout_seconds)
    finally:
        conn.close()


def run(
    config: SweeperConfig,
    *,
    service_factory: Callable[[], AdmissionService] | None = None,
    wait: Callable[[float], bool] = Event().wait,
) -> None:
    while True:
        if service_factory is None:
            _production_sweep(config.timeout_seconds)
        else:
            service_factory().expire_requests(config.timeout_seconds)
        if config.once or wait(config.interval_seconds):
            return


def main() -> None:
    run(SweeperConfig.from_env())


if __name__ == "__main__":
    main()
