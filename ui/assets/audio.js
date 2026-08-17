/* #153 Experience Layer — mute / master volume / reduced-motion, and a
 * tiny synthesized SFX set.
 *
 * MVP boundary (experience-contract.md): "mute / master volume /
 * reduced-motion must exist" is a hard requirement; real sound design is
 * explicitly "nice / follow-up, must not block Campaign delivery." This
 * repo has no audio asset pipeline at all (no `deploy/`/`config/` audio
 * tooling), so rather than license or produce real SFX files, cues are a
 * few short WebAudio-synthesized tones -- zero binary assets, zero
 * licensing surface, trivially swappable for real sound design later
 * without any caller-facing API change (`playCue` stays the same shape).
 */

const STORAGE_KEY = "purplescope.experience.audio-prefs.v1";

const DEFAULT_PREFS = { muted: false, volume: 0.6, reducedMotion: false };

/** Coerces an arbitrary object (parsed JSON, or a caller's patch merged
 * over current prefs) into a valid prefs shape. Shared by `getAudioPrefs`
 * and `setAudioPrefs` so both give the same guarantee: the value returned
 * (and the value ever persisted to localStorage) is always in range --
 * `setAudioPrefs({volume: 5}).volume` must read `1`, not `5`, without
 * waiting for a subsequent `getAudioPrefs()` round-trip to clamp it. */
function _normalize(raw) {
  return {
    muted: typeof raw?.muted === "boolean" ? raw.muted : DEFAULT_PREFS.muted,
    volume: typeof raw?.volume === "number"
      ? Math.min(1, Math.max(0, raw.volume)) : DEFAULT_PREFS.volume,
    reducedMotion: typeof raw?.reducedMotion === "boolean"
      ? raw.reducedMotion : DEFAULT_PREFS.reducedMotion,
  };
}

export function getAudioPrefs() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    return _normalize(JSON.parse(raw));
  } catch (cause) {
    // Malformed/foreign localStorage content must not crash the room's
    // shared screen -- fall back to defaults, same "fail soft" posture as
    // every other presentation-only piece of this layer.
    console.error("audio prefs unreadable, using defaults", cause);
    return { ...DEFAULT_PREFS };
  }
}

export function setAudioPrefs(patch) {
  const next = _normalize({ ...getAudioPrefs(), ...patch });
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

/* ---------- 提示音（WebAudio 合成，無 binary 素材）---------- */

//: cue kind -> a short (<1s) tone recipe. Frequencies chosen only to be
//: distinguishable by ear, not for any musical reason.
const TONE_BY_KIND = {
  critical_alert: { frequency: 880, durationMs: 220, type: "square" },
  objective_complete: { frequency: 660, durationMs: 260, type: "sine" },
  phase_transition: { frequency: 440, durationMs: 400, type: "triangle" },
};

let sharedContext = null;

function audioContext() {
  // Browsers refuse to start an AudioContext before a user gesture; lazily
  // creating it on first `playCue` call (always in response to something
  // the Instructor or the live SSE stream triggered post-load) avoids an
  // upfront "AudioContext was not allowed to start" console warning.
  if (sharedContext === null) {
    const Ctor = window.AudioContext || window.webkitAudioContext;
    sharedContext = Ctor ? new Ctor() : null;
  }
  return sharedContext;
}

/** Plays the tone for `kind`, if one exists and the user hasn't muted.
 * Silently does nothing for kinds with no tone, muted prefs, or a browser
 * without WebAudio -- a missing/blocked sound must never be an error on
 * the room's shared screen. */
export function playCue(kind) {
  const recipe = TONE_BY_KIND[kind];
  if (!recipe) return;
  const prefs = getAudioPrefs();
  if (prefs.muted || prefs.volume <= 0) return;
  const ctx = audioContext();
  if (!ctx) return;

  const oscillator = ctx.createOscillator();
  const gain = ctx.createGain();
  oscillator.type = recipe.type;
  oscillator.frequency.value = recipe.frequency;
  gain.gain.value = prefs.volume * 0.2; // headroom -- a synthesized square wave at full volume clips
  oscillator.connect(gain).connect(ctx.destination);
  oscillator.start();
  oscillator.stop(ctx.currentTime + recipe.durationMs / 1000);
}
