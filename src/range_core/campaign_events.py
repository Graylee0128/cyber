"""Campaign Core Event writes (#153 Experience Layer).

`range_core` has always read `core_events` as a published table contract
(`telemetry.py`'s `CoreEventFeed`) without importing `purple.store.events`.
This module is the one deliberate exception: it *writes* three narrative
event types (`campaign.phase_transition`, `campaign.announcement`,
`campaign.objective_complete`) into the same table, via the same raw-SQL
seam `telemetry.py` already uses to read it -- not by importing
`purple.store.events.CoreEventStore`, which would break the `range_core`
does-not-import-`purple` boundary `test_boundary.py` enforces.

This is safe, not a workaround: `purple/store/db.py`'s own schema comment
says the `seq` allocation is "serialized for every writer, including direct
SQL writers" (the advisory-lock-guarded `next_core_event_stream_seq()`
default already anticipates this). Decided explicitly for #153 rather than
reaching for a parallel event bus, so Experience Layer cues ride the one
existing, working, clearance-filtered SSE pipeline
(`event_stream.py` / `GET /api/events/live`) instead of duplicating it.

The three `campaign.*` event_types are registered `public` in
`disclosure/event_visibility.py` -- deliberately: `public` is the floor of
`VISIBILITY_RANK`, so every open SSE subscriber (Battleboard/Blue SOC/Purple/
Instructor) receives them regardless of clearance; each surface renders its
own presentation from the same event (see `experience-contract.md`).

Does not validate against `purple.harness.schema.assert_core_event` (same
import-boundary reason) -- `_assert_campaign_event` below is a small,
locally-duplicated version of the same shape check, same trade
`telemetry.py`'s module docstring already made ("a duplicated ~10-line
SELECT is the cheaper side of that trade").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import psycopg
from disclosure import expected_visibility
from psycopg.types.json import Jsonb

#: Same INSERT shape as `purple.store.events.INSERT` (same table, same
#: columns) -- `event_id, lifecycle` is the table's PK, so a resent
#: `event_id` (practically impossible here: freshly minted per call, unlike
#: Grafana's webhook retries which intentionally reuse `event_id`) is a
#: no-op rather than a duplicate row.
_INSERT = """
INSERT INTO core_events
    (event_id, lifecycle, event_type, exercise_id, scenario_id, observed_at, action_id, event)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id, lifecycle) DO NOTHING
RETURNING event_id, seq
"""

_REQUIRED_FIELDS = frozenset(
    {
        "event_id", "exercise_id", "scenario_id", "event_type", "lifecycle",
        "severity", "source", "team", "technique", "target", "observed_at",
        "visibility", "action_id",
    }
)
_LIFECYCLES = frozenset({"firing", "resolved"})


class CampaignEventRejected(ValueError):
    """A `campaign.*` event failed its own shape check before ever reaching
    the database -- a bug in this module's own `build_*` functions, not a
    caller input problem (callers never construct the event dict directly)."""


def _assert_campaign_event(event: dict[str, Any]) -> None:
    """Slimmed-down, `range_core`-local echo of
    `purple.harness.schema.assert_core_event` -- not the same rules
    file, deliberately (see module docstring): only the checks that a
    `build_*` function in this module could plausibly get wrong."""
    missing = _REQUIRED_FIELDS - event.keys()
    if missing:
        raise CampaignEventRejected(f"missing required field(s): {sorted(missing)}")
    unknown = event.keys() - _REQUIRED_FIELDS
    if unknown:
        raise CampaignEventRejected(f"unknown field(s): {sorted(unknown)}")
    if event["lifecycle"] not in _LIFECYCLES:
        raise CampaignEventRejected(f"invalid lifecycle: {event['lifecycle']!r}")
    expected = expected_visibility(event["event_type"])
    if expected is None:
        raise CampaignEventRejected(f"unknown event_type: {event['event_type']!r}")
    if event["visibility"] != expected:
        raise CampaignEventRejected(
            f"visibility {event['visibility']!r} does not match "
            f"event_type {event['event_type']!r} (expected {expected!r})"
        )
    observed_at = event["observed_at"]
    if not isinstance(observed_at, str) or datetime.fromisoformat(observed_at).tzinfo is None:
        raise CampaignEventRejected(
            f"observed_at must be a timezone-aware ISO-8601 string, got {observed_at!r}"
        )


def _mint_event_id() -> str:
    return "evt-campaign-" + uuid.uuid4().hex


def _base_event(
    *, event_type: str, exercise_id: str, scenario_id: str, now: datetime,
    severity: str, target: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": _mint_event_id(),
        "exercise_id": exercise_id,
        "scenario_id": scenario_id,
        "event_type": event_type,
        "lifecycle": "firing",
        "severity": severity,
        "source": "instructor-console",
        # Sentinel, not a gameplay team: these events represent narrative
        # state the Instructor authored, not something Red or Blue did.
        # No consumer in this repo filters Core Events on `event["team"]`
        # (only `AdmissionPlayer`/roster team, an unrelated concept, does).
        "team": "instructor",
        # Narrative events carry no MITRE technique and correspond to no
        # registered attack-chain action.
        "technique": None,
        "action_id": None,
        "target": target,
        "observed_at": now.isoformat(),
        "visibility": expected_visibility(event_type),
    }


def build_phase_transition_event(
    exercise_id: str, scenario_id: str, *, chapter: str | None, phase: str,
    label: str, bgm_phase: str | None, animation: str | None, now: datetime,
) -> dict[str, Any]:
    """Reveal/advance Chapter, Final countdown, and End/result reveal are
    all this one event with a different `phase` (experience-contract.md's
    Instructor-as-GM MVP list treats them as one operation)."""
    return _base_event(
        event_type="campaign.phase_transition",
        exercise_id=exercise_id, scenario_id=scenario_id, now=now,
        severity="info",
        target={
            "chapter": chapter, "phase": phase, "label": label,
            "bgm_phase": bgm_phase, "animation": animation,
        },
    )


def build_announcement_event(
    exercise_id: str, scenario_id: str, *, text: str, severity: str, now: datetime,
) -> dict[str, Any]:
    return _base_event(
        event_type="campaign.announcement",
        exercise_id=exercise_id, scenario_id=scenario_id, now=now,
        severity=severity,
        target={"text": text, "severity": severity},
    )


def build_objective_complete_event(
    exercise_id: str, scenario_id: str, *, objective_id: str, evaluation: str, now: datetime,
) -> dict[str, Any]:
    return _base_event(
        event_type="campaign.objective_complete",
        exercise_id=exercise_id, scenario_id=scenario_id, now=now,
        severity="info",
        target={"objective_id": objective_id, "evaluation": evaluation},
    )


def append_campaign_event(conn: psycopg.Connection, event: dict[str, Any]) -> tuple[str, int] | None:
    """Validate, then insert. Returns `(event_id, seq)` if this was a
    genuinely new row, `None` if `ON CONFLICT DO NOTHING` swallowed a
    duplicate `event_id` (should not happen in practice -- `_mint_event_id`
    is fresh per call -- but the table's own PK is the actual authority,
    not this module's assumption)."""
    _assert_campaign_event(event)
    row = conn.execute(
        _INSERT,
        (
            event["event_id"], event["lifecycle"], event["event_type"],
            event["exercise_id"], event["scenario_id"], event["observed_at"],
            event["action_id"], Jsonb(event),
        ),
    ).fetchone()
    return (row[0], row[1]) if row is not None else None
