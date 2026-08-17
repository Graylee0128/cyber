/* #153 Campaign — presentation-only content for the Experience Layer.
 *
 * Deliberately not a backend concern: cue *label text* is non-authoritative
 * per experience-contract.md's iron rule (turning the whole Experience
 * Layer off must not change gameplay). Keeping copy tweaks in this one
 * reviewable frontend file means a content-design change (a funnier meme,
 * a renamed chapter) never touches `range_core`.
 *
 * Sourced verbatim from each chapter's "Q7 值得投影的 major event" section
 * in `docs/campaign/chapters/*.md` -- update both together if either drifts.
 */

/** scenario_id -> chapter. Every `attack.detected` Core Event carries
 * `scenario_id`, never `chapter` (that's a doc/campaign concept, not a
 * `Scenario` schema field -- see `src/range_core/scenarios.py`). */
export const CHAPTER_BY_SCENARIO = {
  "shopdb-credential-pivot": "CH1",
  "campus-poster-foothold": "CH2",
  "campus-preview-metadata-pivot": "CH3",
  "campus-diagnostics-persistence": "CH4",
  "campus-student-records-idor": "FINAL",
};

/** chapter -> { major_event, critical_alert } public-safe copy for
 * `attack.detected`-derived cues. FINAL has no `critical_alert` entry on
 * purpose: docs/campaign/chapters/FINAL-the-leak.md's Q7 explicitly says
 * critical_alert is suppressed there -- no signature rule exists for
 * FINAL's actual payoff steps (T1213/T1567), so Blue SOC must not be
 * alert-washed for it. If a technique with a real rule (e.g. T1087) still
 * fires at high severity, this table's absence just falls through to a
 * generic label rather than fabricating chapter-specific urgency. */
export const MAJOR_EVENT_COPY = {
  CH1: { major_event: "Initial Access Detected", critical_alert: "SQLi Burst Firing" },
  CH2: { major_event: "Foothold Established", critical_alert: "Web Shell Spawned" },
  CH3: { major_event: "Internal Pivot Detected", critical_alert: "Anomalous Egress to Metadata" },
  CH4: { major_event: "Persistence Established", critical_alert: "Unexpected Shell + Cron Change" },
  FINAL: { major_event: "Mass Data Access — Data Exfiltration" },
};

/** chapter -> objective_complete flavor label (校園化 meme copy, README
 * tone: 80% immersive / 20% humor). Falls back to a generic label when a
 * chapter has none defined (CH1 intentionally has none -- "capture the
 * flag" carries the moment on its own). */
export const OBJECTIVE_COMPLETE_COPY = {
  CH2: "ROOT ACCESS ACQUIRED",
  CH3: "STOLEN KEY IN HAND",
  CH4: "GHOST INSTALLED",
  FINAL: "THE LEAK IS OUT",
};

export const DEFAULT_MAJOR_EVENT_LABEL = "Attack Detected";
export const DEFAULT_CRITICAL_ALERT_LABEL = "Critical Alert";
export const DEFAULT_OBJECTIVE_COMPLETE_LABEL = "Objective Complete";
