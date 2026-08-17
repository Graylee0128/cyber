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

// 藍隊一段兩台，各一個 ttyd（WS8 spec §5.4／§6.1，決策 22）：
// 對外主機 a＝DMZ、內部主機 b＝flag。授權靠 session cookie＋這個固定代號
// （admission/service.py 的 validate_endpoints 把 blue 鎖死成 {"a","b"}），
// 不是每台機器各發一組密鑰，所以這裡照代號給人看得懂的標籤即可。
const TERMINAL_LABELS = {
  a: "對外主機 a（DMZ）",
  b: "內部主機 b（Flag）",
};

function renderTerminal() {
  const terminals = new URLSearchParams(location.search).getAll("terminal");
  // 同 player/app.js renderTerminal 的理由：`render()` 掛在 `poll(8, refresh)`
  // 上，iframe 若跟著無條件重建，兩台 ttyd 的 WebSocket session 就每 8 秒
  // 被砍線重連一次。用 join 比對整個清單（不是只比對長度），terminal=a&b
  // 換成 terminal=b&a 這種同集合不同序也視為未變——但這裡的網址是玩家自己
  // 從領位結果導轉來的固定順序，實務上不會發生，寫成集合比對只是不留洞。
  const key = terminals.join(",");
  if (state.renderedTerminals === key) return;
  state.renderedTerminals = key;

  const host = clear($("#terminal-host"));
  if (terminals.length === 0) {
    host.append(el("div", { class: "empty", text:
      "未指定終端機。這一頁的網址要帶 ?terminal=a&terminal=b（一段兩台，各一個 ttyd），"
      + "代號由 Admission 在領位時發給你。" }));
    return;
  }
  for (const terminal of terminals) {
    host.append(el("div", { class: "terminal-block" }, [
      el("div", { class: "terminal-label", text: TERMINAL_LABELS[terminal] ?? terminal }),
      el("iframe", {
        class: "terminal-frame",
        src: `/terminal/${encodeURIComponent(terminal)}`,
        title: `seat terminal (${terminal})`,
      }),
    ]));
  }
}

setInterval(() => {
  $("#timer").textContent = state.exercise?.ends_at
    ? `⏱ ${countdown(state.exercise.ends_at)} 剩餘`
    : "—";
}, 1000);

poll(8, refresh);
