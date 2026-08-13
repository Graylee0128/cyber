"""Telemetry-driven objective completion (#33 WS5-3).

`CoreEventFeed` reads `core_events` as a **published table contract**, the
same pattern `purple.receiver.exercise_context` uses in the other direction
for `exercises`. It does not import `purple.store.events.CoreEventStore`:
that store lives in Z-MGMT, and a `range_core` caller importing it would
break the one rule `scenarios.py` states in its module docstring ("this
package deliberately does not import from purple"). A duplicated ~10-line
SELECT is the cheaper side of that trade — verified against `purple`
imports repo-wide by `tests/range_core/test_boundary.py`.

Deployment reality (declared, not worked around): as of #33, no Grafana
rule in `deploy/grafana/provisioning/alerting/rules.yaml` carries an
`action_id` label, so every Core Event today has `action_id: null` and
nothing here can match. That wiring is WS4/deploy territory for whichever
scenario lands first (WS2 §6.3) — this module's domain logic is fully
testable today against synthesized events, and `sync_telemetry_objectives`
reports *why* nothing completed via `skipped` reasons instead of going
silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from range_core.objectives import ObjectiveStore
from range_core.scenarios import Scenario


@dataclass(frozen=True)
class CoreEventFeed:
    """Read-only query against the `core_events` table contract."""

    conn: psycopg.Connection

    def firing_events_for_actions(
        self, exercise_id: str, action_ids: frozenset[str]
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        """`(event_id, event)` pairs for firing events whose `action_id` is
        one of `action_ids`. Uses the existing `core_events_action_idx`
        partial index (`exercise_id, action_id) WHERE action_id IS NOT NULL`)."""
        if not action_ids:
            return ()
        rows = self.conn.execute(
            """
            SELECT event_id, event FROM core_events
            WHERE exercise_id = %s AND lifecycle = 'firing' AND action_id = ANY(%s)
            ORDER BY recorded_at
            """,
            (exercise_id, list(action_ids)),
        ).fetchall()
        return tuple((row[0], row[1]) for row in rows)


@dataclass(frozen=True)
class SkippedEvent:
    event_id: str
    reason: Literal["no_source_ip", "unknown_source"]


@dataclass(frozen=True)
class MatchResult:
    completions: tuple[tuple[str, str, str], ...]  # (player_id, objective_id, event_id)
    skipped: tuple[SkippedEvent, ...]


def match_telemetry_objectives(
    scenario: Scenario,
    roster: dict[str, str],
    events: tuple[tuple[str, dict[str, Any]], ...],
) -> MatchResult:
    """Pure: (scenario, {source_ip: player_id}, [(event_id, event)]) -> matches.

    Rules (#33 WS5-3 acceptance criteria):
      - Only `telemetry`-type objectives are candidates; a `submission`
        objective cannot declare `telemetry_signal` at all (schema-enforced
        in `scenarios.py`), so this function structurally cannot complete one.
      - Match on `action_id` equality only — never technique, never time
        proximity (same rule `purple.store.events.detections_by_action` enforces).
      - `response.*` events are excluded even if they happen to carry a
        matching `action_id`: a `response.executed` event's `target.source_ip`
        is the *blocked attacker's* address, and counting it would let a
        Blue containment action award Red points — the cross-team coupling
        WS1 §1.1's non-zero-sum rule forbids.
      - No `target.source_ip` on the event -> no completion, ever (never
        team-wide, never "the only registered player") — recorded as
        `skipped(reason="no_source_ip")`.
      - `source_ip` present but not on the roster -> `skipped(reason="unknown_source")`.
    """
    signal_by_action: dict[str, str] = {
        objective.telemetry_signal.action_id: objective.id
        for objective in scenario.objectives
        if objective.evaluation == "telemetry" and objective.telemetry_signal is not None
    }

    completions: list[tuple[str, str, str]] = []
    skipped: list[SkippedEvent] = []
    for event_id, event in events:
        event_type = event.get("event_type", "")
        if isinstance(event_type, str) and event_type.startswith("response."):
            continue
        action_id = event.get("action_id")
        objective_id = signal_by_action.get(action_id) if isinstance(action_id, str) else None
        if objective_id is None:
            continue
        source_ip = (event.get("target") or {}).get("source_ip")
        if not source_ip:
            skipped.append(SkippedEvent(event_id=event_id, reason="no_source_ip"))
            continue
        player_id = roster.get(source_ip)
        if player_id is None:
            skipped.append(SkippedEvent(event_id=event_id, reason="unknown_source"))
            continue
        completions.append((player_id, objective_id, event_id))

    return MatchResult(completions=tuple(completions), skipped=tuple(skipped))


def sync_telemetry_objectives(
    conn: psycopg.Connection, scenario: Scenario, exercise_id: str
) -> MatchResult:
    """Re-scan all matching Core Events for this exercise and insert any new
    completions. No watermark/cursor: idempotency comes from `ON CONFLICT DO
    NOTHING` on the completion PK, not from careful bookkeeping of "already
    scanned" state, so there is no cursor to corrupt or skip. A 30-minute
    exercise has tens of relevant events — a full re-scan on every call is
    cheap at this scale (same argument WS5 spec makes for scoring)."""
    action_ids = frozenset(
        objective.telemetry_signal.action_id
        for objective in scenario.objectives
        if objective.evaluation == "telemetry" and objective.telemetry_signal is not None
    )
    events = CoreEventFeed(conn).firing_events_for_actions(exercise_id, action_ids)

    roster_rows = conn.execute(
        "SELECT source_ip::text, player_id FROM exercise_players WHERE exercise_id = %s",
        (exercise_id,),
    ).fetchall()
    roster = {row[0]: row[1] for row in roster_rows}

    result = match_telemetry_objectives(scenario, roster, events)

    objectives_by_id = {objective.id: objective for objective in scenario.objectives}
    store = ObjectiveStore(conn)
    for player_id, objective_id, event_id in result.completions:
        store.complete_by_telemetry(
            exercise_id, player_id, objective_id, event_id, objective=objectives_by_id[objective_id]
        )
    return result
