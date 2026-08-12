#!/usr/bin/env python3
"""Verify P1's four normalized fields through real aggregating LogQL queries."""

from __future__ import annotations

import argparse

from purple.telemetry_fields import app_contract, falco_contract, verify_contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loki-url", default="http://localhost:3100")
    parser.add_argument("--app", required=True, choices=("vulnerable-app", "range-target"))
    parser.add_argument("--falco", action="store_true")
    args = parser.parse_args()

    contracts = [app_contract(args.app)]
    if args.falco:
        contracts.append(falco_contract())
    for contract in contracts:
        counts = verify_contract(contract, args.loki_url)
        print(f"{contract.name}: " + ", ".join(f"{key}={value:g}" for key, value in counts.items()))
    print("P1 four-field contract PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
