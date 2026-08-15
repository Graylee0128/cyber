/* Instructor Console —— 唯一的全知角色。
 *
 * 這是四個畫面裡唯一看得到 raw event 的。它走 instructor gateway，clearance 3，
 * 所以 SSE 推過來的事件沒有經過任何欄位遮蔽 —— technique、rule 全在。
 *
 * 攻防進度那一格用的是 `revealed=true` 的投影：Q4 的延遲揭露只約束公開層，
 * 教官本來就該即時看到真實狀態。那個 `revealed` 由 gateway 前綴決定，不是
 * 這支程式帶參數帶出來的（見 deploy/ui/nginx.conf）。
 */

import {
  Gateway, humanize, $, el, clear, renderEmpty, showBanner, clockTime, poll,
} from "../assets/api.js";

const api = new Gateway("instructor");
const banner = $("#banner");
const MAX_RAW = 60;

const state = { exercise: null, scenarios: [], raw: [], score: null, attacks: [] };

/* ---------- 生命週期 ---------- */

async function refresh() {
  try {
    if (state.scenarios.length === 0) {
      state.scenarios = await api.core("/api/scenarios");
      const select = clear($("#scenario-select"));
      for (const scenario of state.scenarios) {
        select.append(el("option", { value: scenario.id, text: `${scenario.id} — ${scenario.name}` }));
      }
    }

    const exercise = await api.core("/api/exercises/current");
    state.exercise = exercise;
    renderLifecycle();

    if (!exercise) {
      $("#subtitle").textContent = "目前沒有進行中的演練";
      clear($("#scores"));
      clear($("#chain"));
      return;
    }
    $("#subtitle").textContent =
      `演練 ${exercise.exercise_id}　·　scenario ${exercise.scenario_id}`;
    showBanner(banner, "");

    state.score = await api.core("/api/score");
    renderScores();

    try {
      const board = await api.evaluation(`/api/exercises/${exercise.exercise_id}/battleboard`);
      state.attacks = board.events ?? [];
    } catch (error) {
      if (error.status !== 409) throw error;
      state.attacks = [];
    }
    renderChain();

    await loadAdmissionAlerts();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

function renderLifecycle() {
  const host = clear($("#lifecycle"));
  const exercise = state.exercise;
  if (!exercise) {
    renderEmpty(host, "沒有進行中的演練。");
    $("#btn-reset").disabled = true;
    $("#btn-start").disabled = false;
    return;
  }
  $("#btn-reset").disabled = false;
  $("#btn-start").disabled = true;
  for (const [key, value] of [
    ["exercise_id", exercise.exercise_id],
    ["scenario", exercise.scenario_id],
    ["狀態", exercise.state ?? "—"],
    ["開始", clockTime(exercise.started_at)],
    ["結束", clockTime(exercise.ends_at)],
  ]) {
    host.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: key }),
      el("span", { text: String(value ?? "—") }),
    ]));
  }
}

$("#btn-start").addEventListener("click", async () => {
  const scenarioId = $("#scenario-select").value;
  const raw = window.prompt(
    "以逗號分隔列出紅隊玩家的來源 IP（例如 10.167.30.11,10.167.30.12）。\n"
    + "來源 IP 是名冊的鍵：flag 提交與 hint 都靠它歸屬到個別玩家。"
  );
  if (raw === null) return;
  const players = raw.split(",").map((ip) => ip.trim()).filter(Boolean)
    .map((ip, index) => ({ player_id: `red-${String(index + 1).padStart(2, "0")}`, source_ip: ip }));
  if (players.length === 0) {
    showBanner(banner, "至少要有一位玩家才能開始演練。", "info");
    return;
  }
  try {
    await api.core("/api/exercises/start", {
      method: "POST",
      body: { scenario_id: scenarioId, players },
    });
    showBanner(banner, "演練已開始。", "info");
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

$("#btn-prepare").addEventListener("click", async () => {
  try {
    await api.core("/api/exercises/prepare", {
      method: "POST",
      body: { scenario_id: $("#scenario-select").value },
    });
    showBanner(banner, "已預備。玩家現在可以經 Admission 領位。", "info");
  } catch (error) {
    // prepare 只收 admission 身分（Range Core 的 require_identity 明文擋下），
    // 所以 instructor gateway 打這條會 403 —— 照實說，不要讓按鈕看起來壞掉。
    showBanner(banner, error.status === 403
      ? "預備演練只接受 Admission 服務身分呼叫，教官控台不是那條路徑的合法呼叫者。"
      : humanize(error));
  }
});

$("#btn-reset").addEventListener("click", async () => {
  if (!window.confirm("結束並重置目前這場演練？演練狀態會被清掉，稽核軌跡保留。")) return;
  try {
    await api.core("/api/exercises/reset", { method: "POST", body: {} });
    showBanner(banner, "演練已重置。", "info");
    state.raw = [];
    renderRaw();
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

/* ---------- 維運動作 ---------- */

$("#btn-sync").addEventListener("click", async () => {
  try {
    const result = await api.core("/api/objectives/sync", { method: "POST" });
    const host = clear($("#ops-result"));
    host.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "完成" }),
      el("span", { text: String(result.completed?.length ?? 0) }),
    ]));
    // `skipped` 帶著原因，就是為了讓「掃了但什麼都沒完成」不會被讀成靜默的零。
    for (const skip of result.skipped ?? []) {
      host.append(el("div", { class: "note", text: `略過 ${skip.event_id}：${skip.reason}` }));
    }
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

$("#btn-latency").addEventListener("click", async () => {
  if (!state.exercise) return;
  try {
    const result = await api.evaluation(
      `/api/exercises/${state.exercise.exercise_id}/latency`, { method: "POST" }
    );
    const host = clear($("#ops-result"));
    for (const [mode, summary] of Object.entries(result.modes ?? {})) {
      host.append(el("div", { class: "kv-row" }, [
        el("span", { class: "k", text: `${mode}（${summary.sample_count} 筆）` }),
        el("span", { text: `MTTD p50 ${summary.mttd_p50_ms}ms　·　MTTR p50 ${summary.mttr_p50_ms}ms` }),
      ]));
    }
  } catch (error) {
    // 樣本不足是 409。那不是壞掉，是「還不能當最終量測交付」。
    showBanner(banner, error.status === 409
      ? `延遲摘要尚不可產出：${error.detail}`
      : humanize(error), error.status === 409 ? "info" : "error");
  }
});

async function loadAdmissionAlerts() {
  const host = clear($("#admission-alerts"));
  try {
    const alerts = await api.admission("/alerts");
    if (alerts.length === 0) {
      renderEmpty(host, "無告警。");
      return;
    }
    for (const alert of alerts) {
      host.append(el("div", { class: "kv-row" }, [
        el("span", { class: "k mono", text: alert.seat_id }),
        el("span", { text: alert.reason }),
      ]));
    }
  } catch (error) {
    renderEmpty(host, `讀不到 Admission 告警：${humanize(error)}`);
  }
}

/* ---------- 攻防進度與比分 ---------- */

const DOT_CLASS = { "○": "", "🟡": "unknown", "🔴": "missed", "🟢": "detected" };

function renderChain() {
  const host = clear($("#chain"));
  if (state.attacks.length === 0) {
    renderEmpty(host, "Registry 未凍結或無註冊動作。");
    return;
  }
  for (const attack of state.attacks) {
    host.append(el("span", {
      class: `dot badge ${DOT_CLASS[attack.state] ?? ""}`,
      text: `${attack.state} ${attack.attack_label}`,
    }));
  }
}

function renderScores() {
  const host = clear($("#scores"));
  host.append(el("div", { class: "stat-row" }, [
    el("span", { style: "color:var(--red)", text: "RED" }),
    el("span", { class: "val", text: String(state.score?.red?.total ?? 0) }),
  ]));
  for (const player of state.score?.red?.players ?? []) {
    const done = (player.objectives ?? []).filter((o) => (o.awarded ?? 0) > 0).length;
    host.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k mono", text: player.player_id }),
      el("span", { text: `${player.total} 分　·　objective ${done}/${(player.objectives ?? []).length}` }),
    ]));
  }
  host.append(el("div", { class: "stat-row" }, [
    el("span", { style: "color:var(--blue)", text: "BLUE" }),
    el("span", { class: "val", text: String(state.score?.blue?.total ?? 0) }),
  ]));
}

/* ---------- Raw Event ---------- */

function pushRaw(event, seq) {
  state.raw.unshift({ seq, event });
  state.raw = state.raw.slice(0, MAX_RAW);
  renderRaw();
}

function renderRaw() {
  const host = clear($("#raw"));
  if (state.raw.length === 0) {
    renderEmpty(host, "尚無事件。");
    return;
  }
  for (const row of state.raw) {
    host.append(el("div", { class: "raw", text:
      `#${row.seq}  ${JSON.stringify(row.event)}` }));
  }
}

const liveDot = $("#live");
api.stream({
  onEvent: pushRaw,
  onOpen: () => { liveDot.className = "dot-live on"; },
  onDrop: () => { liveDot.className = "dot-live off"; },
});

/* ---------- Grafana 可見性（#126）---------- *
 * 不走 Gateway：這是無身分限制的 liveness passthrough（見
 * deploy/ui/default.conf.template 的 /health/grafana），跟主要 refresh
 * poll 分開、互不阻塞——Grafana 掛掉不該連帶讓演練狀態也顯示不出來。 */
const grafanaDot = $("#grafana-live");
async function checkGrafanaHealth() {
  try {
    const response = await fetch("/health/grafana");
    grafanaDot.className = response.ok ? "dot-live on" : "dot-live off";
  } catch {
    grafanaDot.className = "dot-live off";
  }
}

/* ---------- 登出（#126）---------- */

$("#btn-logout").addEventListener("click", async () => {
  try {
    await api.admission("/instructor/logout", { method: "POST" });
  } catch (error) {
    // session 可能本來就過期了——清 cookie、轉頁這件事仍要做到。
    console.error("logout failed", error);
  }
  location.href = "../instructor-login/";
});

renderRaw();
poll(8, refresh);
poll(10, checkGrafanaHealth);
