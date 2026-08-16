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
      await loadPreparationStatus();
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
  // #143 item 5：光把「開始演練」disable 掉不夠顯眼——教官忘記按重置時，
  // 危險的不是他自己再按一次開始（那顆按鈕本來就按不下去），是**玩家**不知情
  // 地對著這場舊演練繼續操作，flag／hint 就此歸屬到錯的場次。這則提示要讓
  // 教官在打算開新場的那一刻，看得到「現在還卡著哪一場、要按哪顆才能清掉」。
  host.append(el("div", { class: "note", text:
    `已有演練在跑（${exercise.exercise_id}）。要開新場請先按下方「結束並重置」—— `
    + "忘記這一步，玩家會不知情地繼續對著這場舊的操作，flag／hint 歸屬到它身上。" }));
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
    const exercise = await api.core("/api/exercises/start", {
      method: "POST",
      body: { scenario_id: scenarioId, players },
    });
    await freezeActionRegistry(exercise.exercise_id, scenarioId);
    showBanner(banner, "演練已開始。", "info");
    refresh();
  } catch (error) {
    // #160：這顆按鈕走的是「手打 IP」legacy 路徑，跟「預備＋Admission 領位」
    // 互斥——409 且訊息提到 already prepared 時，不要照搬後端內部訊息，
    // 直接告訴教官正確的下一步在哪裡，並重抓一次讓那顆按鈕現身。
    if (error.status === 409 && error.detail?.includes("already prepared")) {
      showBanner(
        banner,
        "這個 scenario 已經按過「預備」——手打 IP 這條路跟預備互斥。"
        + "請改用下方「開始已預備的演練」，或先「取消預備」再重試這顆按鈕。",
        "info",
      );
      refresh();
      return;
    }
    showBanner(banner, humanize(error));
  }
});

/* ---------- Action Registry（#143 item 2）----------
 * register／freeze 是 P2 evaluation 的分母來源（`purple/evaluation/action_registry.py`
 * 的 docstring：「the only source of the P2 denominator」）——沒有凍結，Battleboard
 * 的攻防進度與 Purple Console 的涵蓋率就永遠拿不到資料。purple_platform_plan.md
 * §Q5 早就拍板「不需要另建介面」：分母該隨演練開始自動產生，不是教官另外按的
 * 一顆按鈕。所以掛在「開始演練」成功之後，不是新增獨立的 UI 元件。
 *
 * 用演練 exercise_id 而非某個獨立輸入 —— seed 用的是 scenario 自帶的
 * `action_registry_seed()`，教官在這裡沒有東西要填。 */
async function freezeActionRegistry(exerciseId, scenarioId) {
  try {
    await api.evaluation(`/api/exercises/${exerciseId}/actions`, {
      method: "POST",
      body: { scenario_id: scenarioId },
    });
    await api.evaluation(`/api/exercises/${exerciseId}/actions/freeze`, { method: "POST" });
  } catch (error) {
    // 分母沒凍成不該讓「演練已開始」看起來像失敗了——Range Core 那頭已經是
    // 真的在跑，把這裡的失敗獨立講清楚，教官才知道是涵蓋率表會有問題，
    // 不是演練沒開起來。
    showBanner(
      banner,
      `演練已開始，但 Action Registry 凍結失敗（${humanize(error)}）——`
      + "Battleboard 攻防進度與 Purple Console 涵蓋率暫時算不出來。",
      "error",
    );
  }
}

$("#btn-prepare").addEventListener("click", async () => {
  // #143 項目 1：prepare 只收 Range Core 的 admission 服務身分，教官控台
  // 自己打必定 403——改經 Admission 代打（它本來就持有那個身分）。
  // exercise_id 由 Range Core 生成，這裡原樣顯示，不讓教官自己編號。
  try {
    const result = await api.admission("/prepare", {
      method: "POST",
      body: { scenario_id: $("#scenario-select").value },
    });
    showBanner(banner, `已預備 exercise_id=${result.exercise_id}。玩家現在可以經 Admission 領位。`, "info");
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
    // 409 = 已經有一筆 prepared（多半是上次忘記複製 exercise_id）。立刻重抓一次
    // lifecycle 區塊，把既有那筆連同「取消預備」按鈕撈出來，教官不用自己查資料庫（#163）。
    if (error.status === 409) refresh();
  }
});

/* ---------- 查詢／開始／取消已 prepared 的場次（#163、#160）----------
 * `prepare` 的 Banner 只顯示一次，教官忘記複製 exercise_id 或關掉分頁後就
 * 找不回來，之前唯一的救援手段是直接連資料庫。這裡在「沒有進行中的演練」
 * 時順便查一次目前是否卡著一筆 prepared，讓教官能看到它、取消它——
 * 或者（#160）真的把它變成 running，這件事在這之前完全沒有 UI 入口：
 * 唯一存在的「開始演練」按鈕走的是跟 `prepare` 互斥的另一條路
 * （手打紅隊 IP，見上面 `#btn-start`），從來沒有任何按鈕會呼叫
 * `start_prepared`（`POST /api/exercises/start {exercise_id}`）。 */
async function loadPreparationStatus() {
  const host = $("#lifecycle");
  try {
    const prepared = await api.admission("/prepared");
    // 兩條路徑互斥：既然已經預備了，手打 IP 那顆按鈕在這裡按下去只會 409，
    // 直接關掉它比讓教官自己撞牆更清楚（#160 AC：互斥關係要對使用者可見）。
    $("#btn-start").disabled = true;
    host.append(el("div", { class: "note", text:
      `已有預備中的演練：${prepared.exercise_id}（scenario ${prepared.scenario_id}）——`
      + "上方「開始演練」（手打 IP）跟這筆預備互斥，已停用。玩家經 Admission 領位後，"
      + "按下面「開始已預備的演練」即可，不用再輸入任何 IP。" }));
    // `.row` 給 `gap: 8px`（見 base.css）——兩顆按鈕直接 append 到 `#lifecycle`
    // 沒有這層會黏在一起，跟上方工具列（也是 `.row`）的間距對不起來。
    host.append(el("div", { class: "row" }, [
      el("button", {
        class: "primary",
        text: "開始已預備的演練",
        onclick: () => startPrepared(prepared.exercise_id, prepared.scenario_id),
      }),
      el("button", {
        class: "danger",
        text: "取消預備",
        onclick: () => cancelPreparation(prepared.exercise_id),
      }),
    ]));
  } catch (error) {
    // 404 = 目前沒有 prepared，這是正常狀態，不用顯示任何東西。
    if (error.status !== 404) {
      host.append(el("div", { class: "note", text: `查詢預備狀態失敗：${humanize(error)}` }));
    }
  }
}

async function startPrepared(exerciseId, scenarioId) {
  // 不跳 prompt、不用手打 IP——紅隊名冊已經在 `admission_players` 裡了
  // （玩家經 Admission 領位時寫入的），Range Core 的 `start_prepared` 會
  // 自己去撈，這裡只需要帶 `exercise_id`（不能帶 `players`，見
  // range_core/api.py::start_exercise 的驗證）。
  try {
    const exercise = await api.core("/api/exercises/start", {
      method: "POST",
      body: { exercise_id: exerciseId },
    });
    await freezeActionRegistry(exercise.exercise_id, scenarioId);
    showBanner(banner, "演練已開始（已預備場次）。", "info");
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

async function cancelPreparation(exerciseId) {
  if (!window.confirm(`取消預備 ${exerciseId}？取消後才能重新按「預備」。`)) return;
  try {
    await api.admission(`/prepared/${encodeURIComponent(exerciseId)}`, { method: "DELETE" });
    showBanner(banner, "已取消預備。", "info");
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

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
      loadCopilotSummary([]);
      return;
    }
    for (const alert of alerts) {
      host.append(el("div", { class: "kv-row" }, [
        el("span", { class: "k mono", text: alert.seat_id }),
        el("span", { text: alert.reason }),
      ]));
    }
    loadCopilotSummary(alerts);
  } catch (error) {
    renderEmpty(host, `讀不到 Admission 告警：${humanize(error)}`);
  }
}

/* ---------- SOC Copilot（#133）----------
 * 純呈現層：把上面已經拿到的 Admission 告警唸成一段話，不另外查任何資料、
 * 不寫回任何欄位。AI 沒起或逾時時 summary 是 null，畫面就留空——這是正常
 * 回應，不是錯誤，所以不進 banner、不算進 catch。 */
async function loadCopilotSummary(alerts) {
  const host = clear($("#copilot-summary"));
  const playerStatuses = alerts.map((alert) => ({
    player_id: alert.seat_id,
    current_action: alert.reason,
  }));
  try {
    const { summary } = await api.copilotSummary(playerStatuses);
    if (!summary) {
      renderEmpty(host, "（AI 摘要目前無法產生，不影響其餘功能）");
      return;
    }
    host.append(el("div", { class: "note", text: summary }));
  } catch (error) {
    // 這條路徑只有教官前綴才有；打不到通常代表部署沒接上 evaluation-engine，
    // 跟 Admission 告警本身讀不讀得到是兩件事，所以獨立 renderEmpty，不丟進
    // 主要的 refresh() catch 攪亂其餘畫面的錯誤訊息。
    renderEmpty(host, "（SOC Copilot 暫不可用，不影響其餘功能）");
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
