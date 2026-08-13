#!/usr/bin/env python3
"""Verify P1's four normalized fields through real aggregating LogQL queries."""

from __future__ import annotations

import argparse
import json
import time

from purple.telemetry_fields import (
    FieldContractError,
    app_contract,
    falco_contract,
    query_scalar,
    verify_contract,
)


def read_counts(contract, base_url):
    return {field: query_scalar(base_url, query) for field, query in contract.queries.items()}


def wait_for_increase(contract, base_url, baseline, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    while True:
        counts = read_counts(contract, base_url)
        stale = [field for field, count in counts.items() if count <= baseline[field]]
        if not stale:
            return counts
        if time.monotonic() >= deadline:
            raise FieldContractError(
                f"{contract.name}: fresh action did not increase {', '.join(stale)}"
            )
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loki-url", default="http://localhost:3100")
    parser.add_argument("--app", required=True, choices=("vulnerable-app", "range-target"))
    parser.add_argument("--falco", action="store_true")
    parser.add_argument("--capture-falco-baseline")
    parser.add_argument("--falco-baseline")
    parser.add_argument("--wait-seconds", type=float, default=0)
    args = parser.parse_args()

    if args.capture_falco_baseline:
        counts = read_counts(falco_contract(), args.loki_url)
        with open(args.capture_falco_baseline, "w", encoding="utf-8") as stream:
            json.dump(counts, stream)
        return 0

    contracts = [app_contract(args.app)]
    if args.falco:
        contracts.append(falco_contract())
    for contract in contracts:
        if contract.name == "falco" and args.falco_baseline:
            with open(args.falco_baseline, encoding="utf-8") as stream:
                baseline = json.load(stream)
            counts = wait_for_increase(contract, args.loki_url, baseline, args.wait_seconds)
            print(f"{contract.name}: " + ", ".join(f"{key}={value:g}" for key, value in counts.items()))
            continue
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                counts = verify_contract(contract, args.loki_url)
                break
            except FieldContractError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        print(f"{contract.name}: " + ", ".join(f"{key}={value:g}" for key, value in counts.items()))
    print("P1 four-field contract PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
