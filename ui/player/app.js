/* Player Portal（Red）—— 紅隊玩家視角。
 *
 * 藍隊在 `blue.html`，是**另一個頁面**而不是這頁的一個分頁：一頁同時扮兩個
 * 身分就得同時持有兩個 gateway，而 red 的 clearance 是 0、blue 是 1 —— 那等於
 * 給紅隊玩家一個「切一下就升級可見範圍」的按鈕。身分綁在入口上。
 *
 * ## 這頁刻意不顯示的東西
 *
 * `GET /api/scenarios` 回的是完整 scenario **扣掉 hint 文字**，也就是說
 * `attack_chain`（每一步的 MITRE technique 與描述）也在回應裡。那是攻擊鏈，
 * WS2 spec §4.2 明訂 briefing 只給任務目標、不給攻擊鏈 —— 所以這裡只讀
 * `objectives` 與任務中繼資料，一個字都不渲染 `attack_chain`。
 *
 * 前端不渲染不等於沒外洩（devtools 打開就看到），這是後端該補的遮蔽，
 * 已在 ui/README.md 的「已知缺口」記錄。這裡至少不主動把它貼到畫面上。
 */

import {
  Gateway, humanize, $, el, clear, renderEmpty, showBanner, countdown, poll,
} from "../assets/api.js";

const api = new Gateway("red");
const banner = $("#banner");

const state = {
  exercise: null,
  scenario: null,
  score: null,
  hints: new Map(),     // objective_id -> [{hint_index, penalty_percent}]
  revealed: new Map(),  // `${objective_id}:${index}` -> text
  pendingHint: null,
  briefing: null,        // { scenario_id, content } | null
};

/* ---------- 載入 ---------- */

async function refresh() {
  try {
    const exercise = await api.core("/api/exercises/current");
    state.exercise = exercise;
    if (!exercise) {
      $("#subtitle").textContent = "目前沒有進行中的演練";
      showBanner(banner, "等待教官開始演練。", "info");
      renderEmpty($("#objectives"), "演練尚未開始。");
      return;
    }

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
  const scenario = state.scenario;
  $("#subtitle").textContent = state.exercise
    ? `演練 ${state.exercise.exercise_id}`
    : "—";

  if (!scenario) {
    renderEmpty($("#objectives"), "找不到這場演練的 scenario 定義。");
    return;
  }

  $("#mission-name").textContent = scenario.name;
  $("#mission-meta").textContent =
    `難度 ${scenario.difficulty}　·　時長 ${scenario.duration}　·　`
    + `目標主機 ${scenario.targets.map((t) => t.host).join("、")}`;

  renderBriefing(scenario.id);
  renderObjectives();
  renderScore();
  renderFlagOptions();
  renderTerminal();
}

/* ---------- Briefing（#153，關 ui/README.md 已知缺口 #5）---------- */

function renderBriefing(scenarioId) {
  const target = $("#mission-briefing");
  if (state.briefing?.scenario_id === scenarioId) {
    target.textContent = state.briefing.content;
    return;
  }
  target.textContent = "";  // 換場景時先清空，避免短暫顯示上一場的簡報
  loadBriefing(scenarioId);
}

async function loadBriefing(scenarioId) {
  try {
    const briefing = await api.core(`/api/scenarios/${encodeURIComponent(scenarioId)}/briefing`);
    // 拿到回應時使用者可能已經換場景（poll 交錯），只在還是同一場才落地渲染。
    if (state.scenario?.id === scenarioId) {
      state.briefing = briefing;
      $("#mission-briefing").textContent = briefing.content;
    }
  } catch (error) {
    console.error("briefing unavailable", scenarioId, error);
  }
}

/* ---------- Objective ---------- */

function awardedFor(objectiveId) {
  const objectives = (state.score?.red?.players ?? []).flatMap((p) => p.objectives ?? []);
  return objectives.find((o) => o.objective_id === objectiveId) ?? null;
}

function renderObjectives() {
  const host = clear($("#objectives"));
  for (const objective of state.scenario.objectives) {
    const award = awardedFor(objective.id);
    const done = (award?.awarded ?? 0) > 0;

    const body = el("div", { class: "body" }, [
      el("div", { class: "title", text: objective.id }),
      el("div", { class: "meta", text:
        `${objective.points} 分　·　`
        + (objective.evaluation === "submission" ? "提交 flag 判定" : "遙測自動判定")
        + (award?.penalty_percent ? `　·　已扣 ${award.penalty_percent}%` : "") }),
    ]);

    const hints = state.hints.get(objective.id);
    if (hints === undefined) {
      loadHintCosts(objective.id);
    } else {
      for (const hint of hints) {
        const key = `${objective.id}:${hint.hint_index}`;
        const text = state.revealed.get(key);
        if (text) {
          body.append(el("div", { class: "hint-text", text }));
        } else {
          body.append(el("button", {
            text: `需要提示？（扣 ${hint.penalty_percent}%）`,
            onclick: () => askHint(objective.id, hint),
          }));
        }
      }
    }

    host.append(el("div", { class: "obj" }, [
      el("div", { class: "check", text: done ? "✅" : "⬜" }),
      body,
    ]));
  }
}

async function loadHintCosts(objectiveId) {
  state.hints.set(objectiveId, []);  // 先佔位，避免同一輪重複請求
  try {
    const costs = await api.core(`/api/hints?objective_id=${encodeURIComponent(objectiveId)}`);
    state.hints.set(objectiveId, costs);
    renderObjectives();
  } catch (error) {
    console.error("hint costs unavailable", objectiveId, error);
  }
}

/* ---------- Hint 確認（扣分金額先講）---------- */

function askHint(objectiveId, hint) {
  state.pendingHint = { objectiveId, hint };
  $("#hint-cost").textContent =
    `${objectiveId} 的第 ${hint.hint_index + 1} 則提示，會扣掉這個 objective 的 ${hint.penalty_percent}% 分數。`;
  $("#hint-modal").hidden = false;
}

$("#hint-cancel").addEventListener("click", () => {
  state.pendingHint = null;
  $("#hint-modal").hidden = true;
});

$("#hint-confirm").addEventListener("click", async () => {
  const pending = state.pendingHint;
  $("#hint-modal").hidden = true;
  state.pendingHint = null;
  if (!pending) return;
  try {
    const result = await api.core("/api/hints", {
      method: "POST",
      body: { objective_id: pending.objectiveId, hint_index: pending.hint.hint_index },
    });
    state.revealed.set(`${pending.objectiveId}:${pending.hint.hint_index}`, result.text);
    renderObjectives();
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

/* ---------- 分數與 Flag ---------- */

function renderScore() {
  $("#score").textContent = String(state.score?.red?.total ?? 0);
  const players = state.score?.red?.players ?? [];
  $("#score-note").textContent = players.length > 1
    ? `全隊 ${players.length} 名玩家的合計。個人分數依來源 IP 歸屬。`
    : "";
}

function renderFlagOptions() {
  const select = clear($("#flag-objective"));
  const submittable = state.scenario.objectives.filter((o) => o.evaluation === "submission");
  if (submittable.length === 0) {
    select.append(el("option", { text: "本 scenario 無提交型 objective", value: "" }));
    $("#flag-submit").disabled = true;
    return;
  }
  $("#flag-submit").disabled = false;
  for (const objective of submittable) {
    select.append(el("option", { value: objective.id, text: objective.id }));
  }
}

$("#flag-submit").addEventListener("click", async () => {
  const objectiveId = $("#flag-objective").value;
  const flag = $("#flag-input").value.trim();
  const result = $("#flag-result");
  if (!objectiveId || !flag) {
    result.textContent = "請選擇 objective 並填入 flag。";
    return;
  }
  try {
    const response = await api.core("/api/submissions", {
      method: "POST",
      body: { objective_id: objectiveId, flag },
    });
    result.textContent = response.accepted ? "✅ 正確，已記錄。" : "❌ 不正確。";
    if (response.accepted) {
      $("#flag-input").value = "";
      refresh();
    }
  } catch (error) {
    result.textContent = humanize(error);
  }
});

/* ---------- 終端機 ---------- */

function renderTerminal() {
  const host = clear($("#terminal-host"));
  const terminal = new URLSearchParams(location.search).get("terminal");
  if (!terminal) {
    host.append(el("div", { class: "empty", text:
      "未指定終端機。這一頁的網址要帶 ?terminal=<座位的終端機代號>，"
      + "代號由 Admission 在領位時發給你。" }));
    return;
  }
  // 連線隔離不是這一頁保證的：`/terminal/<id>` 由 Z-EDGE 的 nginx 先打
  // `auth_request` 問 Admission「這個 session 擁不擁有這台終端機」，答不出來
  // 就 403。前端只負責把面板嵌進來。
  host.append(el("iframe", {
    class: "terminal-frame",
    src: `/terminal/${encodeURIComponent(terminal)}`,
    title: "seat terminal",
  }));
}

/* ---------- 計時 ---------- */

setInterval(() => {
  $("#timer").textContent = state.exercise?.ends_at
    ? `⏱ ${countdown(state.exercise.ends_at)} 剩餘`
    : "—";
}, 1000);

poll(8, refresh);
