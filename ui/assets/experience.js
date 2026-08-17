/* #153 Experience Layer — Experience Projection (docs/campaign/experience-contract.md).
 *
 * Pure functions, no DOM: `mapEventToCue` turns one Core Event (already
 * clearance-masked server-side, exactly what a surface's `api.stream()`
 * handler already receives) into a cue object, or `null` if this event
 * isn't cue-worthy. Callers (Battleboard/Blue SOC's own SSE handlers) own
 * rendering; this module never touches the DOM and never calls back into
 * the API -- consistent with the contract's iron rule (turn this whole
 * layer off, core gameplay must still work).
 *
 * `mutable_core` is always `false`: structurally guaranteed by never
 * reading or writing anything beyond the one event object passed in.
 */

import {
  CHAPTER_BY_SCENARIO,
  MAJOR_EVENT_COPY,
  OBJECTIVE_COMPLETE_COPY,
  DEFAULT_MAJOR_EVENT_LABEL,
  DEFAULT_CRITICAL_ALERT_LABEL,
  DEFAULT_OBJECTIVE_COMPLETE_LABEL,
} from "./campaign-content.js";

/** The six MVP kinds (experience-contract.md's cue table). Exported so
 * callers can validate/branch exhaustively without hardcoding the list
 * twice. */
export const CUE_KINDS = [
  "phase_transition", "major_event", "objective_complete",
  "critical_alert", "countdown", "reveal",
];

/** Stable per-event id for dedup/animation binding. The Core Event schema
 * is closed (`purple.harness.schema.REQUIRED_FIELDS`) and has no `cue_id`
 * field, so this is *derived* from `(event_id, lifecycle)` -- already the
 * table's own primary key, already stable and unique. */
export function cueId(event) {
  return `${event.event_id}:${event.lifecycle}`;
}

/** severity strings observed in this repo's Grafana rules (deploy/grafana/
 * provisioning/alerting/rules.yaml) that should read as a `critical_alert`
 * rather than a softer `major_event`. */
const HIGH_SEVERITIES = new Set(["high", "critical"]);

function attackDetectedCue(event, seq) {
  const chapter = CHAPTER_BY_SCENARIO[event.scenario_id] ?? null;
  const copy = (chapter && MAJOR_EVENT_COPY[chapter]) || {};
  const isCritical = HIGH_SEVERITIES.has(event.severity);
  if (isCritical && !copy.critical_alert) {
    // FINAL deliberately has no critical_alert copy (see
    // campaign-content.js) -- fall through to major_event rather than
    // fabricate urgency the design explicitly withholds for this chapter.
    return {
      cue_id: cueId(event), source_event_seq: seq, kind: "major_event",
      visibility: "public", chapter, phase: null,
      presentation: { label: copy.major_event ?? DEFAULT_MAJOR_EVENT_LABEL, severity: event.severity },
      mutable_core: false,
    };
  }
  return {
    cue_id: cueId(event), source_event_seq: seq,
    kind: isCritical ? "critical_alert" : "major_event",
    // attack.detected is always public (disclosure/event_visibility.py);
    // critical_alert's role:blue "richer" version is a presentation
    // choice each surface makes locally from this same public event, not
    // a data-shape difference (see campaign_events.py's module docstring
    // for why this needed no backend change).
    visibility: "public", chapter, phase: null,
    presentation: {
      label: isCritical
        ? (copy.critical_alert ?? DEFAULT_CRITICAL_ALERT_LABEL)
        : (copy.major_event ?? DEFAULT_MAJOR_EVENT_LABEL),
      severity: event.severity,
    },
    mutable_core: false,
  };
}

function phaseTransitionCue(event, seq) {
  const target = event.target ?? {};
  return {
    cue_id: cueId(event), source_event_seq: seq, kind: "phase_transition",
    visibility: "public", chapter: target.chapter ?? null, phase: target.phase ?? null,
    presentation: {
      label: target.label ?? "", severity: "info",
      bgm_phase: target.bgm_phase ?? null, animation: target.animation ?? null,
    },
    mutable_core: false,
  };
}

function announcementCue(event, seq) {
  const target = event.target ?? {};
  return {
    cue_id: cueId(event), source_event_seq: seq, kind: "major_event",
    visibility: "public", chapter: null, phase: null,
    presentation: { label: target.text ?? "", severity: target.severity ?? "info" },
    mutable_core: false,
  };
}

function objectiveCompleteCue(event, seq) {
  const target = event.target ?? {};
  const chapter = CHAPTER_BY_SCENARIO[event.scenario_id] ?? null;
  const label = (chapter && OBJECTIVE_COMPLETE_COPY[chapter]) || DEFAULT_OBJECTIVE_COMPLETE_LABEL;
  return {
    cue_id: cueId(event), source_event_seq: seq, kind: "objective_complete",
    visibility: "public", chapter, phase: null,
    presentation: { label, severity: "info", objective_id: target.objective_id ?? null },
    mutable_core: false,
  };
}

/** Core Event -> cue, or `null` if this event isn't cue-worthy. Never
 * throws on a malformed/unexpected event -- an unrecognized `event_type`
 * is exactly the "not a cue" case, not an error. */
export function mapEventToCue(event, seq) {
  switch (event?.event_type) {
    case "campaign.phase_transition": return phaseTransitionCue(event, seq);
    case "campaign.announcement": return announcementCue(event, seq);
    case "campaign.objective_complete": return objectiveCompleteCue(event, seq);
    case "attack.detected": return attackDetectedCue(event, seq);
    default: return null;
  }
}

/** Rate-limits how often a given cue `kind` may play a sound effect.
 * Centralized here (not duplicated per surface) so the pacing gate's
 * "critical SFX ≤ once per 90s" (docs/campaign/dry-run-template.md) is
 * enforced once, consistently, regardless of which surfaces are open.
 *
 * `now` is an injected timestamp (ms), not `Date.now()` internally, so
 * this stays a pure, testable function -- callers pass `Date.now()` in
 * production. */
const DEFAULT_SFX_INTERVAL_MS = { critical_alert: 90_000 };

export function createSfxLimiter(intervals = DEFAULT_SFX_INTERVAL_MS) {
  const lastPlayedAt = new Map();
  return function shouldPlaySfx(kind, now) {
    const interval = intervals[kind];
    if (!interval) return true; // ungated kinds always play
    const last = lastPlayedAt.get(kind);
    if (last !== undefined && now - last < interval) return false;
    lastPlayedAt.set(kind, now);
    return true;
  };
}
