/* Battleboard —— 公開層。教室大螢幕、紅藍雙方、所有人都看得到。
 *
 * 這個畫面的每一個限制都是刻意的，不是還沒做完：
 *
 * 1. **走 `red` gateway**（clearance 0 = public）。不是因為只有紅隊看，而是
 *    因為公開層的可見範圍就等於最低那一級 —— 用 instructor gateway 再叫前端
 *    「別顯示」是假的遮蔽，devtools 打開就看到。
 * 2. **技法一律 `Attack #N`**，且標籤由後端產生（`purple.battleboard.sanitize`）。
 *    同一個 scenario 會拿給下一組隊伍重打，秀 MITRE 編號等於提前公布考題。
 * 3. **不顯示 hit/miss**。回合結束前公開層只有中性狀態 —— 在藍隊自己的 SOC
 *    判定出來之前就把「有沒有被打穿」投影到牆上，等於考試中把答案寫在黑板上，
 *    並且汙染正在被量測的 MTTD。
 * 4. **不放裸百分比**，只放看得見分母的分數形式。
 */

import { Gateway, humanize, $, el, clear, showBanner, clockTime, countdown, poll } from "../assets/api.js";
import { mapEventToCue, createSfxLimiter } from "../assets/experience.js";
import { playCue, getAudioPrefs, setAudioPrefs } from "../assets/audio.js";

const api = new Gateway("red");
const banner = $("#banner");
const MAX_TIMELINE_ROWS = 40;

const state = { exercise: null, timeline: [], attacks: [] };

const PHASE_LABEL = {
  briefing: "簡報中", initial: "初始入侵", escalation: "升級中",
  critical: "危急", final: "最後衝刺", debrief: "賽後總結",
};

/* ---------- #153 Campaign 章節橫幅 ---------- */

function renderCampaignBanner() {
  const campaign = state.exercise?.campaign;
  const bannerEl = $("#campaign-banner");
  if (!campaign) {
    bannerEl.classList.remove("on");
    return;
  }
  bannerEl.classList.add("on");
  $("#campaign-chapter").textContent = campaign.chapter ? `📖 ${campaign.chapter}` : "";
  $("#campaign-phase").textContent = PHASE_LABEL[campaign.phase] ?? campaign.phase;
}

/* ---------- 攻防進度 ---------- */

const DOT_CLASS = {
  "○": "pending",
  "🟡": "progress",
  "🔴": "breached",
  "🟢": "defended",
};

const DOT_LABEL = {
  "○": "進行中",
  "🟡": "進行中",
  "🔴": "紅隊得手",
  "🟢": "已偵測",
};

function renderChain() {
  const host = clear($("#chain"));
  if (state.attacks.length === 0) {
    host.append(el("div", { class: "empty", text: "尚未註冊攻擊動作。" }));
    return;
  }
  for (const attack of state.attacks) {
    const marker = attack.state;
    host.append(el("span", {
      class: `dot ${DOT_CLASS[marker] ?? "pending"}`,
      text: `${marker} ${attack.attack_label} ${DOT_LABEL[marker] ?? ""}`.trim(),
    }));
  }

  const pending = state.attacks.filter((a) => a.disclosure === "pending").length;
  $("#disclosure-note").textContent = pending > 0
    ? `${pending} 項尚未揭露：回合結束前，公開層只顯示中性狀態。`
      + "「有沒有被偵測到／被打穿」的具體結果不在這裡公布 —— "
      + "那個答案要由藍隊自己的 SOC 產出，先貼在牆上會汙染正在量測的 MTTD。"
    : "本回合結果已揭露。技法編號仍為匿名標籤：真實 MITRE 編號只在賽後報告與非公開畫面出現。";
}

/* ---------- 統計 ---------- */

function renderDefense() {
  const host = clear($("#defense"));
  const revealed = state.attacks.filter((a) => a.disclosure === "revealed").length;
  const total = state.attacks.length;

  const rows = [
    ["註冊攻擊動作", total === 0 ? "—" : String(total)],
    ["已揭露", total === 0 ? "—" : `${revealed} / ${total}`],
    ["時間軸事件", String(state.timeline.length)],
  ];
  for (const [label, value] of rows) {
    host.append(el("div", { class: "stat-row" }, [
      el("span", { text: label }),
      el("span", { class: "val", text: value }),
    ]));
  }

  $("#defense-note").textContent =
    "不放「已偵測 3/4」這類防禦計數：在揭露前，那個分子就是尚未公開的答案本身。"
    + "分數形式一律看得見分母，不用裸百分比。";
}

/* ---------- 時間軸 ---------- */

function pushTimelineEvent(event) {
  state.timeline.unshift({
    at: event.observed_at ?? event.recorded_at ?? null,
    type: event.event_type ?? "event",
    lifecycle: event.lifecycle ?? "",
  });
  state.timeline = state.timeline.slice(0, MAX_TIMELINE_ROWS);
  renderTimeline();
  renderDefense();
}

function renderTimeline() {
  const host = clear($("#timeline"));
  if (state.timeline.length === 0) {
    host.append(el("div", { class: "empty", text: "尚無事件。" }));
    return;
  }
  for (const row of state.timeline) {
    const detected = row.type.includes("detected");
    host.append(el("div", { class: "timeline-row" }, [
      el("span", { class: "t", text: clockTime(row.at) }),
      el("span", { text: detected ? "🔴 偵測到攻擊行為" : `· ${row.type}` }),
    ]));
  }
}

/* ---------- 輪詢 ---------- */

async function refresh() {
  try {
    const exercise = await api.core("/api/exercises/current");
    state.exercise = exercise;
    renderCampaignBanner();
    if (!exercise) {
      $("#scenario").textContent = "無進行中的演練";
      $("#timer").textContent = "—";
      showBanner(banner, "目前沒有進行中的演練。", "info");
      return;
    }
    showBanner(banner, "");
    $("#scenario").textContent = exercise.scenario_id ?? "—";

    const score = await api.core("/api/score");
    $("#score-red").textContent = String(score.red?.total ?? 0);
    $("#score-blue").textContent = String(score.blue?.total ?? 0);

    const objectives = (score.red?.players ?? []).flatMap((p) => p.objectives ?? []);
    const done = objectives.filter((o) => (o.awarded ?? 0) > 0).length;
    $("#objective-fraction").textContent =
      objectives.length === 0 ? "—" : `${done} / ${objectives.length}`;
    $("#objective-bar").style.width =
      objectives.length === 0 ? "0%" : `${Math.round((done / objectives.length) * 100)}%`;

    try {
      // gateway 對公開前綴強制 `revealed=false`，這裡不帶任何參數 ——
      // 前端說了不算，也刻意不給前端一個「看起來可以翻開」的開關。
      const board = await api.evaluation(`/api/exercises/${exercise.exercise_id}/battleboard`);
      state.attacks = board.events ?? [];
    } catch (error) {
      // Registry 未凍結（409）時沒有可陳述的攻防進度，這不是錯誤。
      state.attacks = error.status === 409 ? [] : state.attacks;
      if (error.status !== 409) throw error;
    }
    renderChain();
    renderDefense();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

function tickTimer() {
  const endsAt = state.exercise?.ends_at;
  $("#timer").textContent = endsAt ? `${countdown(endsAt)} 剩餘` : "—";
}

/* ---------- #153 Experience Layer：cue toast + SFX ---------- */

const sfxLimiter = createSfxLimiter();
let cueToastTimer = null;

function showCueToast(cue) {
  const toast = $("#cue-toast");
  toast.textContent = cue.presentation?.label ?? "";
  toast.classList.toggle("critical", cue.kind === "critical_alert");
  toast.classList.toggle("reduced-motion", getAudioPrefs().reducedMotion);
  toast.classList.add("show");
  clearTimeout(cueToastTimer);
  cueToastTimer = setTimeout(() => toast.classList.remove("show"), 3500);

  if (sfxLimiter(cue.kind, Date.now())) playCue(cue.kind);
}

function handleLiveEvent(event, seq) {
  pushTimelineEvent(event);
  const cue = mapEventToCue(event, seq);
  if (cue) showCueToast(cue);
  if (cue?.kind === "phase_transition") {
    // The next poll (≤5s) would pick this up anyway via
    // GET /api/exercises/current -- refreshing state.exercise.campaign
    // immediately from the cue itself just avoids a visible lag on the
    // room's shared screen for the one cue that changes the banner.
    if (state.exercise) {
      state.exercise = {
        ...state.exercise,
        campaign: { ...(state.exercise.campaign ?? {}), chapter: cue.chapter, phase: cue.phase },
      };
      renderCampaignBanner();
    }
  }
}

/* ---------- #153 音訊／動態效果控制（localStorage，逐瀏覽器） ---------- */

function initAudioControls() {
  const prefs = getAudioPrefs();
  const mutedEl = $("#audio-muted");
  const volumeEl = $("#audio-volume");
  const reducedMotionEl = $("#audio-reduced-motion");
  mutedEl.checked = prefs.muted;
  volumeEl.value = String(prefs.volume);
  reducedMotionEl.checked = prefs.reducedMotion;

  mutedEl.addEventListener("change", () => setAudioPrefs({ muted: mutedEl.checked }));
  volumeEl.addEventListener("input", () => setAudioPrefs({ volume: Number(volumeEl.value) }));
  reducedMotionEl.addEventListener("change", () => setAudioPrefs({ reducedMotion: reducedMotionEl.checked }));
}

/* ---------- 串流 ---------- */

const liveDot = $("#live");
api.stream({
  onEvent: handleLiveEvent,
  onOpen: () => { liveDot.className = "dot-live on"; },
  onDrop: () => { liveDot.className = "dot-live off"; },
});

initAudioControls();
renderChain();
renderDefense();
renderTimeline();
poll(5, refresh);
setInterval(tickTimer, 1000);
