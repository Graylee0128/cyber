/* Player Portal（Blue）—— 藍隊玩家的任務 HUD。
 *
 * **這是獨立的一頁，不是紅隊那頁的一個分頁。** 一頁同時扮兩個身分就得同時
 * 持有兩個 gateway，而 red 的 clearance 是 0、blue 是 1 —— 那等於給紅隊玩家
 * 一個「切一下就升級可見範圍」的按鈕。身分綁在入口上，切身分＝換頁。
 *
 * 內容比紅隊那頁薄是設計上的結構性差異，不是沒做完：藍隊側不做個人化計分，
 * KPI 本來就是全場級指標，所以沒有個人 Objective／Hint／Flag 可放。
 */

import {
  Gateway, humanize, $, el, clear, renderEmpty, showBanner, countdown, poll,
} from "../assets/api.js";

const api = new Gateway("blue");
const banner = $("#banner");

const state = { exercise: null, scenario: null, score: null };

async function refresh() {
  try {
    const exercise = await api.core("/api/exercises/current");
    state.exercise = exercise;
    if (!exercise) {
      $("#subtitle").textContent = "目前沒有進行中的演練";
      showBanner(banner, "等待教官開始演練。", "info");
      renderEmpty($("#kpi"), "演練尚未開始。");
      return;
    }
    $("#subtitle").textContent = `演練 ${exercise.exercise_id}`;

    const scenarios = await api.core("/api/scenarios");
    state.scenario = scenarios.find((s) => s.id === exercise.scenario_id) ?? null;
    state.score = await api.core("/api/score");

    showBanner(banner, "");
    render();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

function render() {
  if (state.scenario) {
    $("#mission-name").textContent = `監控中：${state.scenario.name}`;
    $("#mission-meta").textContent =
      `難度 ${state.scenario.difficulty}　·　時長 ${state.scenario.duration}　·　`
      + `受保護主機 ${state.scenario.targets.map((t) => t.host).join("、")}`;
  }

  const blue = state.score?.blue;
  $("#score").textContent = String(blue?.total ?? 0);

  const events = blue?.events ?? [];
  const kpi = clear($("#kpi"));
  const resolved = events.filter((e) => e.resolved).length;
  const contained = events.filter((e) => e.contain_seconds !== null
    && e.contain_seconds !== undefined).length;
  const detectTimes = events
    .map((e) => e.detect_seconds)
    .filter((value) => value !== null && value !== undefined);
  const meanDetect = detectTimes.length === 0
    ? null
    : detectTimes.reduce((sum, value) => sum + value, 0) / detectTimes.length;

  for (const [label, value] of [
    // 分數形式看得見分母 —— 裸比率藏起分母，藍隊自己也判斷不出那個數字準不準。
    ["已處置事件", `${resolved} / ${events.length}`],
    ["已封鎖事件", `${contained} / ${events.length}`],
    ["平均偵測反應", meanDetect === null ? "—" : `${meanDetect.toFixed(1)} 秒`],
  ]) {
    kpi.append(el("div", { class: "stat-row" }, [
      el("span", { text: label }),
      el("span", { class: "val", text: value }),
    ]));
  }

  renderEvents(events);
  renderTerminal();
}

function renderEvents(events) {
  const body = clear($("#event-rows"));
  if (events.length === 0) {
    renderEmpty($("#event-empty"), "尚無事件。");
    return;
  }
  clear($("#event-empty"));
  const round = (value) => (value === null || value === undefined ? "—" : value.toFixed(1));
  for (const event of events) {
    body.append(el("tr", {}, [
      el("td", { class: "mono", text: event.event_id }),
      el("td", { text: round(event.detect_seconds) }),
      el("td", { text: round(event.contain_seconds) }),
      el("td", { text: event.resolved ? "✅" : "—" }),
      el("td", { text: String(event.awarded ?? 0) }),
    ]));
  }
}

function renderTerminal() {
  const host = clear($("#terminal-host"));
  const terminal = new URLSearchParams(location.search).get("terminal");
  if (!terminal) {
    host.append(el("div", { class: "empty", text:
      "未指定終端機。這一頁的網址要帶 ?terminal=<座位的終端機代號>，"
      + "代號由 Admission 在領位時發給你。" }));
    return;
  }
  host.append(el("iframe", {
    class: "terminal-frame",
    src: `/terminal/${encodeURIComponent(terminal)}`,
    title: "seat terminal",
  }));
}

setInterval(() => {
  $("#timer").textContent = state.exercise?.ends_at
    ? `⏱ ${countdown(state.exercise.ends_at)} 剩餘`
    : "—";
}, 1000);

poll(8, refresh);
