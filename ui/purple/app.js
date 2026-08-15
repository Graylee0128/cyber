/* Purple Console —— 畫面一（涵蓋率表）／畫面二（動作下鑽）／Exercise Report。
 *
 * 資料全部來自 Evaluation API，畫面**不重算任何判定**：四態、缺口分類、涵蓋率
 * 都由引擎給，這裡只負責排版（`purple/console/drilldown.py` 的
 * 「畫面與引擎不得各算各的」）。唯一在前端做的聚合是「同一個 technique 底下
 * 多個動作要用哪個狀態代表」，而那是純粹的顯示問題，不是判定。
 */

import { Gateway, humanize, $, $$, el, clear, renderEmpty, showBanner, poll } from "../assets/api.js";

const api = new Gateway("purple");
const banner = $("#banner");

const state = {
  exerciseId: null,
  scenarioId: null,
  registry: null,     // {actions: [{id, technique, description}], frozen_at}
  evaluation: null,   // {metrics, actions: [{action_id, state, level, gap, reason, event_ids}]}
  techniques: new Map(),  // id -> {name, tactic, note}
  latency: null,
  selected: null,
};

/* ---------- 顯示規則 ---------- */

const ACTION_STATE = {
  hit:          { class: "detected", label: "✅ 已偵測" },
  miss:         { class: "missed",   label: "❌ 未偵測" },
  unknown:      { class: "unknown",  label: "⏳ 未知" },
  not_executed: { class: "na",       label: "— 未執行" },
};

const GAP_LABEL = {
  hit: "命中",
  detection_gap: "偵測缺口（看得到卻沒偵測到）",
  visibility_gap: "可見性缺口（來源健康但這筆沒進來）",
  unknown: "無法判定（來源掉線）",
  out_of_scope: "不在範圍（來源未部署）",
};

const LEVEL_NOTE = {
  C1: "C1 · 僅原始訊號",
  C2: "C2 · 已確認，單一來源",
  C3: "C3 · 已確認，跨來源交叉支持",
};

// #126：畫面二 Telemetry 欄——四種標記對應既有的 badge 色票（同 ACTION_STATE 的
// 語意，✅ 綠／❌ 紅／— 灰／⏳ 黃），不是這裡新發明一套配色。
const TELEMETRY_MARK = {
  "✅": { class: "detected", label: "✅" },
  "❌": { class: "missed", label: "❌" },
  "—": { class: "na", label: "—" },
  "⏳": { class: "unknown", label: "⏳" },
};

/** 一個 technique 底下多個動作時，用哪個狀態代表這一列。
 *
 * 取「最壞的那個」：只要有一個動作沒被偵測到，這個 technique 就不算守住了。
 * 反過來取最好的會讓一個 technique 底下九個 miss、一個 hit 顯示成綠色。 */
function worstState(states) {
  for (const candidate of ["miss", "unknown", "hit", "not_executed"]) {
    if (states.includes(candidate)) return candidate;
  }
  return "not_executed";
}

/* ---------- 載入 ---------- */

async function loadCurrentExercise() {
  const exercise = await api.core("/api/exercises/current");
  if (!exercise) {
    state.exerciseId = null;
    return false;
  }
  state.exerciseId = exercise.exercise_id;
  state.scenarioId = exercise.scenario_id;
  $("#subtitle").textContent =
    `演練 ${exercise.exercise_id}　·　scenario ${exercise.scenario_id}`;
  return true;
}

async function refresh() {
  try {
    if (!(await loadCurrentExercise())) {
      $("#subtitle").textContent = "目前沒有進行中的演練";
      showBanner(banner, "沒有進行中的演練，Console 無資料可顯示。", "info");
      renderEmpty($("#coverage-empty"), "等待演練開始。");
      clear($("#coverage-rows"));
      return;
    }

    if (state.techniques.size === 0) {
      const list = await api.evaluation("/api/techniques");
      for (const t of list) state.techniques.set(t.id, t);
    }

    state.registry = await api.evaluation(`/api/exercises/${state.exerciseId}/actions`);

    try {
      state.evaluation = await api.evaluation(`/api/exercises/${state.exerciseId}/evaluation`);
      showBanner(banner, "");
    } catch (error) {
      state.evaluation = null;
      if (error.status === 409) {
        // 未凍結不是「還沒有資料」，是「這個分母還會變」—— 用它算出來的
        // 任何比率都無法判斷準不準，所以這裡不退化成部分顯示。
        showBanner(banner,
          "Action Registry 尚未凍結：分母還會變動，涵蓋率與確認率在凍結前不予顯示。", "info");
      } else {
        throw error;
      }
    }

    state.latency = await api.evaluation(`/api/exercises/${state.exerciseId}/latency`);
    render();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

/* ---------- 畫面一 ---------- */

function render() {
  renderCoverage();
  renderMetrics();
  renderReport();
  if (state.selected) renderDrilldown(state.selected);
}

function renderCoverage() {
  const body = clear($("#coverage-rows"));
  const actions = state.registry?.actions ?? [];
  if (actions.length === 0) {
    renderEmpty($("#coverage-empty"), "這場演練還沒有註冊任何紅隊動作。");
    return;
  }
  clear($("#coverage-empty"));

  const resultOf = new Map(
    (state.evaluation?.actions ?? []).map((r) => [r.action_id, r])
  );

  const byTechnique = new Map();
  for (const action of actions) {
    if (!byTechnique.has(action.technique)) byTechnique.set(action.technique, []);
    byTechnique.get(action.technique).push(action);
  }

  for (const [techniqueId, group] of byTechnique) {
    const meta = state.techniques.get(techniqueId);
    const results = group.map((a) => resultOf.get(a.id)).filter(Boolean);
    const executed = results.some((r) => r.state !== "not_executed");
    const blue = results.length === 0 ? "unknown" : worstState(results.map((r) => r.state));
    const badge = ACTION_STATE[blue] ?? ACTION_STATE.unknown;

    const nameCell = el("td", {}, [
      el("div", { text: meta ? `${techniqueId} ${meta.name}` : techniqueId }),
      // 判讀限制常駐顯示，不藏進 tooltip：限制說明的目的就是防止誤讀，
      // 藏起來有違初衷（purple-console spec Q3 選項 A）。
      meta?.note ? el("div", { class: "note", text: `判讀限制：${meta.note}` }) : null,
    ]);

    const row = el("tr", { class: "clickable" }, [
      nameCell,
      el("td", {}, [executed
        ? el("span", { class: "badge detected", text: "✅ 已執行" })
        : el("span", { class: "badge na", text: "— 未執行" })]),
      el("td", {}, [el("span", { class: `badge ${badge.class}`, text: badge.label })]),
      el("td", { text: String(group.length) }),
    ]);
    row.addEventListener("click", () => openDrilldown(techniqueId));
    body.append(row);
  }
}

function renderMetrics() {
  const host = clear($("#coverage-metrics"));
  const metrics = state.evaluation?.metrics;
  if (!metrics) {
    host.append(el("div", { class: "empty", text: "Registry 未凍結，指標不予計算。" }));
    return;
  }

  const percent = (value) => (value === null || value === undefined
    ? "—"
    : `${(value * 100).toFixed(1)}%`);

  const excluded = Object.entries(metrics.excluded_counts ?? {})
    .map(([reason, count]) => `${reason} ${count}`)
    .join("　·　") || "無";

  for (const [label, value] of [
    ["Action Coverage（動作涵蓋率）", percent(metrics.action_coverage)],
    ["Confirmation Rate（確認率）", percent(metrics.confirmation_rate)],
    ["Alert Volume（告警總量）", String(metrics.alert_volume ?? 0)],
    ["Excluded（排除計數）", excluded],
  ]) {
    host.append(el("div", { class: "metric" }, [
      el("div", { class: "label", text: label }),
      el("div", { class: "value", text: value }),
    ]));
  }
}

/* ---------- 畫面二 ---------- */

function openDrilldown(techniqueId) {
  state.selected = techniqueId;
  renderDrilldown(techniqueId);
  showScreen("drilldown");
}

function renderDrilldown(techniqueId) {
  const panel = clear($("#drilldown-panel"));
  const meta = state.techniques.get(techniqueId);
  const actions = (state.registry?.actions ?? []).filter((a) => a.technique === techniqueId);
  const resultOf = new Map((state.evaluation?.actions ?? []).map((r) => [r.action_id, r]));

  panel.append(el("div", {}, [
    el("div", { style: "font-size:18px;font-weight:600", text:
      meta ? `${techniqueId} · ${meta.name}` : techniqueId }),
    meta?.tactic ? el("div", { class: "sub", text: `tactic：${meta.tactic}` }) : null,
    meta?.note ? el("div", { class: "note", text: `判讀限制：${meta.note}` }) : null,
  ]));

  if (actions.length === 0) {
    panel.append(el("div", { class: "empty", text: "這個技法沒有註冊動作。" }));
    return;
  }

  for (const action of actions) {
    const result = resultOf.get(action.id);
    const badge = ACTION_STATE[result?.state] ?? ACTION_STATE.unknown;

    panel.append(el("div", { class: "section-label", text: `動作 ${action.id}` }));
    panel.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "描述" }),
      el("span", { text: action.description || "—" }),
    ]));
    panel.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "判定" }),
      el("span", { class: `badge ${badge.class}`, text: badge.label }),
    ]));
    panel.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "證據等級" }),
      el("span", { text: result?.level ? LEVEL_NOTE[result.level] ?? result.level : "—" }),
    ]));
    panel.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "缺口分類" }),
      el("span", { text: result?.gap ? (GAP_LABEL[result.gap] ?? result.gap) : "—" }),
    ]));

    const telemetry = result?.telemetry ?? [];
    const telemetryRow = el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "Telemetry" }),
    ]);
    if (telemetry.length === 0) {
      telemetryRow.append(el("span", { text: "—" }));
    } else {
      const list = el("span", { class: "row" });
      for (const mark of telemetry) {
        const style = TELEMETRY_MARK[mark.mark] ?? { class: "neutral", label: mark.mark };
        list.append(el("span", {
          class: `badge ${style.class}`,
          text: `${mark.source_id} ${style.label}`,
        }));
      }
      telemetryRow.append(list);
    }
    panel.append(telemetryRow);

    panel.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "判定理由" }),
      el("span", { text: result?.reason || "—" }),
    ]));

    const eventIds = result?.event_ids ?? [];
    const events = el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "關聯事件" }),
    ]);
    if (eventIds.length === 0) {
      events.append(el("span", { text: "—" }));
    } else {
      const list = el("span", { class: "row" });
      for (const eventId of eventIds) {
        list.append(el("button", {
          class: "link mono",
          text: eventId,
          onclick: () => showEvidence(eventId),
        }));
      }
      events.append(list);
    }
    panel.append(events);
  }

  panel.append(el("div", { class: "section-label", text: "延遲（全場，依 mode 分列）" }));
  panel.append(latencyBlock());
}

function latencyBlock() {
  const modes = state.latency?.modes ?? {};
  const host = el("div");
  const entries = Object.entries(modes);
  if (entries.length === 0) {
    host.append(el("div", { class: "empty", text: "尚未量測（需要足量樣本才會產出摘要）。" }));
    return host;
  }
  for (const [mode, summary] of entries) {
    host.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: `${mode}（${summary.sample_count} 筆）` }),
      el("span", { text:
        `MTTD p50 ${summary.mttd_p50_ms ?? "—"}ms / p95 ${summary.mttd_p95_ms ?? "—"}ms　·　`
        + `MTTR p50 ${summary.mttr_p50_ms ?? "—"}ms / p95 ${summary.mttr_p95_ms ?? "—"}ms` }),
    ]));
    if (summary.notes?.mttd_floor) {
      host.append(el("div", { class: "note", text: summary.notes.mttd_floor }));
    }
    if (summary.notes?.mttr_mode) {
      host.append(el("div", { class: "note", text: summary.notes.mttr_mode }));
    }
  }
  return host;
}

async function showEvidence(eventId) {
  try {
    const bundle = await api.evidence(eventId);
    const lines = (bundle.lines ?? [])
      .map((line) => `${line.timestamp}  [${line.source}]  ${line.line}`)
      .join("\n");
    window.alert(
      `事件 ${bundle.event_id}\n規則：${bundle.rule ?? "—"}\n`
      + `時窗 ${bundle.window_start} → ${bundle.window_end}（${bundle.line_count} 行）\n\n`
      + (lines || "（此 clearance 看不到內容行）")
    );
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

/* ---------- Exercise Report ---------- */

function renderReport() {
  const host = clear($("#report-body"));
  const metrics = state.evaluation?.metrics;
  const results = state.evaluation?.actions ?? [];

  if (!metrics) {
    host.append(el("div", { class: "empty", text:
      "Registry 未凍結，報告不予產出 —— 分母還會變動的報告會被轉寄、被引用。" }));
    return;
  }

  const gaps = results.filter((r) => r.gap && r.gap !== "hit");
  const unknowns = results.filter((r) => r.state === "unknown");

  const row = (key, value) => el("div", { class: "kv-row" }, [
    el("span", { class: "k", text: key }),
    el("span", {}, [value]),
  ]);

  host.append(row("演練", el("span", { text: `${state.exerciseId}　·　${state.scenarioId}` })));
  host.append(row("告警總量", el("span", { text: String(metrics.alert_volume ?? 0) })));

  const gapList = el("span", { class: "row" });
  if (gaps.length === 0) {
    gapList.append(el("span", { text: "無" }));
  } else {
    for (const gap of gaps) {
      gapList.append(el("span", {
        class: `badge ${gap.gap === "detection_gap" ? "missed" : "unknown"}`,
        text: `${gap.action_id}：${GAP_LABEL[gap.gap] ?? gap.gap}`,
      }));
    }
  }
  host.append(row("缺口分類", gapList));

  const unknownDetail = unknowns.length === 0
    ? "0 項"
    : `${unknowns.length} 項　·　原因：` + unknowns.map((u) => u.reason).join("；");
  host.append(row("unknown 數量與原因", el("span", { text: unknownDetail })));

  host.append(row("延遲", latencyBlock()));

  $("#report-note").textContent =
    "這三個欄位（告警總量、缺口分類、unknown 數量與原因）不可省略。"
    + "本頁是即時計算的預覽；#28 的 Exercise Report 是演練結束後的持久化快照，"
    + "資料快照後不隨後續變動 —— 兩者不是同一份東西。";
}

/* ---------- 分頁 ---------- */

function showScreen(name) {
  for (const section of ["coverage", "drilldown", "report"]) {
    $(`#screen-${section}`).hidden = section !== name;
  }
  for (const tab of $$(".tab")) {
    tab.classList.toggle("active", tab.dataset.screen === name);
  }
}

for (const tab of $$(".tab")) {
  tab.addEventListener("click", () => showScreen(tab.dataset.screen));
}
$("#back-to-coverage").addEventListener("click", () => showScreen("coverage"));

poll(10, refresh);
