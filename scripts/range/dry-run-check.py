#!/usr/bin/env python3
"""#153 Campaign — mechanical pre-checks for `docs/campaign/dry-run-template.md`.

**This script does not execute a dry run.** The template's pacing/engagement
items (dead air, transition feel, alert fatigue, meme-copy landing) need a
human in the room and stay exactly as they are: a table for gray to fill by
hand on real hardware. What *is* mechanical -- the template's "Detection /
Purple 校驗" checklist -- is scriptable against the real HTTP APIs, so this
runs it early and catches a wiring regression before burning a human
walkthrough on it.

## What this checks, and why each one is a plain HTTP call

Campaign v1 is five *separate* exercises against the same `range-target`
host, not one exercise spanning five scenarios (`exercises_one_running_idx`
allows exactly one `running` row at a time -- see `src/range_core/exercises.py`).
So this script checks **the one currently-running exercise**, and the human
dry-run runs it once per chapter, in sequence, after that chapter has
actually been attacked:

1. **Evaluation API answers, not 409/503** -- 409 means Action Registry
   isn't frozen yet (the denominator would still be moving), 503 means the
   telemetry backend or `config/scenario-sources.yaml` registration is
   missing. Either is a real blocker to report, not something to paper over.
2. **Intentional-gap guardrail** -- for every technique this chapter's
   `metadata.yaml` lists under `intentional_gaps`, the corresponding
   attack-chain action must never show `state: hit`. This is the one
   invariant that should hold *before* an attack too: if a gap technique is
   ever `hit`, a Grafana rule was mistakenly added for it (dry-run-template.md:
   "若被誤加規則，護欄測試應紅").
3. **Covered telemetry objectives are wired** -- every `evaluation: telemetry`
   objective's `action_id` should appear in the evaluation response at all
   (not silently absent from `actions`, which would mean the action was
   never registered/frozen).
4. **Battleboard answers for both `revealed` states** -- `GET
   .../battleboard?revealed=true` and `revealed=false` both 200 with a
   non-empty `events` list once the exercise has any registered actions at
   all (an empty list is a real gap: the room's shared screen would show
   nothing).

## What this does NOT check (left to the human table)

Pacing (dead air, transition feel), alert fatigue, meme-copy landing,
whether FINAL's silence *reads* as intentional dread rather than as
something being broken. None of that is HTTP-observable.

## Usage

    python scripts/range/dry-run-check.py \\
        --range-core-url http://range-core.example:8000 \\
        --evaluation-url http://evaluation.example:8001 \\
        --instructor-token "$RANGE_CORE_TOKEN_INSTRUCTOR"

Run once per chapter, after that chapter has been played through, before
resetting for the next one.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from range_core.scenarios import ScenarioCatalog  # noqa: E402


class CheckFailure(Exception):
    """One mechanical check failed. Message is the human-readable reason."""


def _get(base_url: str, path: str, token: str) -> tuple[int, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"detail": body}


def _current_exercise(range_core_url: str, token: str) -> dict[str, Any]:
    status, payload = _get(range_core_url, "/api/exercises/current", token)
    if status != 200:
        raise CheckFailure(f"GET /api/exercises/current -> {status}: {payload}")
    if payload is None:
        raise CheckFailure(
            "no running exercise -- start the chapter's exercise before running this check"
        )
    return payload


def _scenario_for(scenario_id: str):
    catalog = ScenarioCatalog.from_directory(REPO / "scenarios")
    scenario = next((s for s in catalog.scenarios if s.id == scenario_id), None)
    if scenario is None:
        raise CheckFailure(f"scenario {scenario_id!r} not found in local scenarios/ catalog")
    return scenario


def check_evaluation_answers(evaluation_url: str, exercise_id: str, token: str) -> dict[str, Any]:
    status, payload = _get(evaluation_url, f"/api/exercises/{exercise_id}/evaluation", token)
    if status == 409:
        raise CheckFailure(
            "evaluation API returned 409 -- Action Registry isn't frozen yet "
            "(POST .../actions then .../actions/freeze, or start the exercise "
            "through the Instructor Console which does this automatically)"
        )
    if status == 503:
        raise CheckFailure(
            f"evaluation API returned 503: {payload.get('detail', payload)} -- "
            "telemetry backend down or scenario not registered in "
            "config/scenario-sources.yaml"
        )
    if status != 200:
        raise CheckFailure(f"GET .../evaluation -> {status}: {payload}")
    print(f"  ok: evaluation API answered ({len(payload.get('actions', []))} actions)")
    return payload


def check_intentional_gaps_never_hit(scenario, evaluation_payload: dict[str, Any]) -> None:
    gap_action_ids = {
        action.id for action in scenario.attack_chain if action.technique in scenario.intentional_gaps
    }
    if not gap_action_ids:
        print("  ok: no intentional_gaps declared for this chapter (nothing to guard)")
        return
    actions_by_id = {a["action_id"]: a for a in evaluation_payload.get("actions", [])}
    violations = [
        action_id for action_id in gap_action_ids
        if actions_by_id.get(action_id, {}).get("state") == "hit"
    ]
    if violations:
        raise CheckFailure(
            f"intentional_gaps guardrail failed -- these action_ids are 'hit' but "
            f"should have zero detection coverage: {sorted(violations)}. "
            "A Grafana rule was likely added by mistake for a technique this "
            "chapter's metadata.yaml deliberately leaves uncovered."
        )
    print(f"  ok: {len(gap_action_ids)} intentional-gap action(s) confirmed never 'hit'")


def check_telemetry_objectives_registered(scenario, evaluation_payload: dict[str, Any]) -> None:
    telemetry_action_ids = {
        objective.telemetry_signal.action_id
        for objective in scenario.objectives
        if objective.evaluation == "telemetry" and objective.telemetry_signal is not None
    }
    if not telemetry_action_ids:
        print("  ok: no telemetry objectives declared for this chapter")
        return
    known_action_ids = {a["action_id"] for a in evaluation_payload.get("actions", [])}
    missing = telemetry_action_ids - known_action_ids
    if missing:
        raise CheckFailure(
            f"telemetry objective action_id(s) missing from Action Registry: "
            f"{sorted(missing)} -- Action Registry wasn't frozen with this "
            "scenario's actions, so these objectives can never complete"
        )
    print(f"  ok: {len(telemetry_action_ids)} telemetry objective action_id(s) registered")


def check_battleboard_both_reveal_states(evaluation_url: str, exercise_id: str, token: str) -> None:
    for revealed in ("false", "true"):
        status, payload = _get(
            evaluation_url, f"/api/exercises/{exercise_id}/battleboard?revealed={revealed}", token
        )
        if status != 200:
            raise CheckFailure(f"GET .../battleboard?revealed={revealed} -> {status}: {payload}")
        events = payload.get("events", [])
        if not events:
            raise CheckFailure(
                f"GET .../battleboard?revealed={revealed} returned an empty events "
                "list -- the room's shared screen would show nothing"
            )
        print(f"  ok: battleboard revealed={revealed} -> {len(events)} event(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range-core-url", required=True, help="e.g. http://range-core:8000")
    parser.add_argument("--evaluation-url", required=True, help="e.g. http://evaluation:8001")
    parser.add_argument("--instructor-token", required=True, help="RANGE_CORE_TOKEN_INSTRUCTOR value")
    args = parser.parse_args()

    try:
        exercise = _current_exercise(args.range_core_url, args.instructor_token)
        scenario_id = exercise["scenario_id"]
        exercise_id = exercise["exercise_id"]
        print(f"Checking exercise {exercise_id} (scenario {scenario_id})...")
        scenario = _scenario_for(scenario_id)

        evaluation_payload = check_evaluation_answers(
            args.evaluation_url, exercise_id, args.instructor_token
        )
        check_intentional_gaps_never_hit(scenario, evaluation_payload)
        check_telemetry_objectives_registered(scenario, evaluation_payload)
        check_battleboard_both_reveal_states(args.evaluation_url, exercise_id, args.instructor_token)
    except CheckFailure as exc:
        print(f"\nFAIL: {exc}", file=sys.stderr)
        return 1

    print(f"\nAll mechanical checks passed for {scenario_id}.")
    print(
        "Reminder: this does not replace the human table in "
        "docs/campaign/dry-run-template.md -- pacing, alert fatigue, and "
        "narrative feel still need a real walkthrough."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
