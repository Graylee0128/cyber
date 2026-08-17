/* Blue SOC Console —— 藍隊的作戰畫面。
 *
 * ## 為什麼這裡看不到 technique
 *
 * 藍隊要**自己判讀**技法並提交（`classify`），答案不能寫在告警上。所以
 * `technique` 欄位在 blue clearance 被整個拿掉、`rule` 被換成 `Detection #N`
 * 這種穩定但不帶語意的標籤 —— rule 名稱本身就是答案的白話版
 * （`SQLInjectionBurst` 就是 T1190）。這是後端組裝回應時做的，不是這裡。
 *
 * ## 為什麼判讀類動作按下去就不能反悔
 *
 * `classify` 每個事件只有一次機會（WS3 spec §4.2）—— 送出後端會回 409。
 * 畫面照實反映：送出前跳確認，送出後把按鈕鎖掉，而不是讓玩家連按到猜對。
 */

import {
  Gateway, humanize, $, $$, el, clear, renderEmpty, showBanner, clockTime, poll,
} from "../assets/api.js";
import { mapEventToCue, createSfxLimiter } from "../assets/experience.js";
import { playCue } from "../assets/audio.js";

const api = new Gateway("blue");
const banner = $("#banner");
const MAX_ALERTS = 100;
const sfxLimiter = createSfxLimiter();

const state = {
  exercise: null,
  alerts: [],        // 最新在前
  selected: null,
  evidence: null,
  score: null,
  judged: new Set(), // 這個瀏覽器工作階段內已送出判讀的 event_id
};

const ACTIONS = [
  { id: "acknowledge", label: "確認接手", hint: "記錄藍隊已看到這筆告警。" },
  { id: "classify", label: "判讀技法", hint: "一個事件只有一次機會，送出後不可更改。", needsTechnique: true },
  { id: "contain", label: "封鎖來源", hint: "會經 Range Core 派送到 Z-MGMT 的 response queue。" },
  { id: "resolve", label: "結案", hint: "" },
  { id: "dismiss", label: "誤報", hint: "" },
];

const ONE_SHOT = new Set(["classify"]);

/* ---------- 告警佇列 ---------- */

function pushAlert(event) {
  if (event.event_type && !String(event.event_type).includes("detected")) return;
  const eventId = event.event_id;
  if (!eventId) return;
  if (state.alerts.some((a) => a.event_id === eventId)) return;
  state.alerts.unshift(event);
  state.alerts = state.alerts.slice(0, MAX_ALERTS);
  renderAlerts();
}

function renderAlerts() {
  const host = clear($("#alerts"));
  if (state.alerts.length === 0) {
    renderEmpty(host, "尚無告警。串流連上後，新的偵測事件會即時出現在這裡。");
    return;
  }
  for (const alert of state.alerts) {
    const severity = alert.severity ?? "low";
    const node = el("div", {
      class: `alert${state.selected === alert.event_id ? " active" : ""}`,
      onclick: () => select(alert.event_id),
    }, [
      el("div", { class: "head" }, [
        el("span", { class: `sev-${severity}`, text: severity.toUpperCase() }),
        el("span", { class: "id", text: alert.event_id }),
      ]),
      el("div", { class: "meta", text:
        `${clockTime(alert.observed_at)}　·　`
        + `${alert.target?.service ?? "—"}　←　${alert.target?.source_ip ?? "—"}`
        + (alert.rule ? `　·　${alert.rule}` : "") }),
    ]);
    host.append(node);
  }
}

/* ---------- #153 Experience Layer：critical-alert pulse + 輕量 cue banner ---------- */

let cueBannerTimer = null;

function showCueBanner(text) {
  const target = $("#cue-banner");
  target.textContent = text;
  target.classList.add("show");
  clearTimeout(cueBannerTimer);
  cueBannerTimer = setTimeout(() => target.classList.remove("show"), 4000);
}

function pulseAlertsHeader() {
  const title = $("#alerts-panel-title");
  title.classList.remove("pulse");
  // Force reflow so re-adding the class restarts the animation even if a
  // second critical_alert cue arrives before the first pulse finished.
  void title.offsetWidth;
  title.classList.add("pulse");
}

function handleLiveEvent(event, seq) {
  pushAlert(event);
  const cue = mapEventToCue(event, seq);
  if (!cue) return;
  if (cue.kind === "critical_alert") {
    // MVP boundary (experience-contract.md): Blue SOC gets critical-alert
    // visual + short SFX only -- objective_complete/major_event get the
    // lighter banner below, no pulse, no sound (Battleboard already plays
    // objective-complete SFX; two surfaces sounding the same room-shared
    // moment would just be noise, not signal).
    pulseAlertsHeader();
    if (sfxLimiter(cue.kind, Date.now())) playCue(cue.kind);
  } else if (cue.kind === "objective_complete" || cue.kind === "major_event") {
    showCueBanner(cue.presentation?.label ?? "");
  }
}

async function select(eventId) {
  state.selected = eventId;
  state.evidence = null;
  renderAlerts();
  renderDetail();
  renderActions();
  try {
    state.evidence = await api.evidence(eventId);
  } catch (error) {
    state.evidence = { error: humanize(error) };
  }
  renderDetail();
}

/* ---------- 事件細節 ---------- */

function renderDetail() {
  const host = clear($("#detail"));
  const alert = state.alerts.find((a) => a.event_id === state.selected);
  if (!alert) {
    renderEmpty(host, "選一筆告警看細節。");
    return;
  }

  const rows = [
    ["事件 ID", alert.event_id],
    ["觀測時間", clockTime(alert.observed_at)],
    ["嚴重度", alert.severity ?? "—"],
    ["服務", alert.target?.service ?? "—"],
    ["來源 IP", alert.target?.source_ip ?? "—"],
    ["偵測規則", alert.rule ?? "（此 clearance 不顯示）"],
  ];
  for (const [key, value] of rows) {
    host.append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: key }),
      el("span", { text: String(value) }),
    ]));
  }

  host.append(el("div", { class: "section-label", text: "證據（raw log 時窗）" }));
  const evidence = state.evidence;
  if (!evidence) {
    host.append(el("div", { class: "empty", text: "讀取中…" }));
    return;
  }
  if (evidence.error) {
    host.append(el("div", { class: "empty", text: evidence.error }));
    return;
  }
  host.append(el("div", { class: "kv-row" }, [
    el("span", { class: "k", text: "時窗" }),
    el("span", { text: `${clockTime(evidence.window_start)} → ${clockTime(evidence.window_end)}（${evidence.line_count} 行）` }),
  ]));
  const lines = evidence.lines ?? [];
  if (lines.length === 0) {
    host.append(el("div", { class: "empty", text: "此 clearance 看不到內容行。" }));
    return;
  }
  for (const line of lines) {
    host.append(el("div", { class: "evidence-line", text:
      `${clockTime(line.timestamp)}  [${line.source}]  ${line.line}` }));
  }
}

/* ---------- 處置動作 ---------- */

function renderActions() {
  const host = clear($("#actions"));
  if (!state.selected) {
    renderEmpty(host, "選一筆告警才能處置。");
    $("#action-note").textContent = "";
    return;
  }

  const row = el("div", { class: "row" });
  for (const action of ACTIONS) {
    const locked = ONE_SHOT.has(action.id) && state.judged.has(state.selected);
    row.append(el("button", {
      class: action.id === "contain" ? "danger" : "",
      text: locked ? `${action.label}（已送出）` : action.label,
      disabled: locked ? "disabled" : null,
      onclick: () => submitAction(action),
    }));
  }
  host.append(row);

  if (ONE_SHOT.has("classify")) {
    host.append(el("div", { class: "row", style: "margin-top:8px" }, [
      el("span", { class: "k", text: "判讀技法" }),
      el("input", { id: "technique-input", placeholder: "例如 T1190", style: "flex:1" }),
    ]));
  }
  $("#action-note").textContent = ACTIONS.map((a) => a.hint).filter(Boolean).join("　");
}

async function submitAction(action) {
  const eventId = state.selected;
  if (!eventId) return;

  const body = { event_id: eventId, action: action.id };
  if (action.needsTechnique) {
    const technique = ($("#technique-input")?.value ?? "").trim();
    if (!technique) {
      showBanner(banner, "判讀動作需要填入技法編號。", "info");
      return;
    }
    if (!window.confirm(
      `判讀為 ${technique}。這個事件只有一次判讀機會，送出後不可更改。確定嗎？`
    )) return;
    body.technique = technique;
  }

  try {
    const result = await api.core("/api/blue-actions", { method: "POST", body });
    if (ONE_SHOT.has(action.id)) state.judged.add(eventId);

    if (action.id === "contain") {
      // 派送失敗不是例外，而是寫回同一列的狀態 —— 計分看的就是這個欄位，
      // 所以畫面必須照實顯示，不能把 failed 說成成功。
      showBanner(banner,
        result.dispatch_status === "dispatched"
          ? "封鎖指令已送達 Z-MGMT 的 response queue。"
          : "封鎖指令派送失敗：這筆不會計分，因為封鎖實際上沒有發生。",
        result.dispatch_status === "dispatched" ? "info" : "error");
    } else {
      showBanner(banner, `已送出 ${action.label}。`, "info");
    }
    renderActions();
    refresh();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

/* ---------- 計分 ---------- */

function renderScoring() {
  const body = clear($("#scoring-rows"));
  const events = state.score?.blue?.events ?? [];
  if (events.length === 0) {
    renderEmpty($("#scoring-empty"), "尚無計分資料。");
    return;
  }
  clear($("#scoring-empty"));
  const round = (value) => (value === null || value === undefined ? "—" : value.toFixed(1));
  for (const event of events) {
    body.append(el("tr", {}, [
      el("td", { class: "mono", text: event.event_id }),
      el("td", { text: round(event.detect_seconds) }),
      el("td", { text: round(event.contain_seconds) }),
      el("td", { text: event.judgement ?? "—" }),
      el("td", { text: event.resolved ? "✅" : "—" }),
      el("td", { text: String(event.awarded ?? 0) }),
    ]));
  }
}

/* ---------- 輪詢與串流 ---------- */

async function refresh() {
  try {
    const exercise = await api.core("/api/exercises/current");
    state.exercise = exercise;
    if (!exercise) {
      $("#subtitle").textContent = "目前沒有進行中的演練";
      return;
    }
    $("#subtitle").textContent =
      `演練 ${exercise.exercise_id}　·　scenario ${exercise.scenario_id}`;
    state.score = await api.core("/api/score");
    renderScoring();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

const liveDot = $("#live");
api.stream({
  onEvent: handleLiveEvent,
  onOpen: () => { liveDot.className = "dot-live on"; },
  onDrop: () => { liveDot.className = "dot-live off"; },
});

for (const tab of $$(".tab")) {
  tab.addEventListener("click", () => {
    for (const other of $$(".tab")) other.classList.toggle("active", other === tab);
    for (const name of ["ops", "scoring", "grafana"]) {
      $(`#screen-${name}`).hidden = name !== tab.dataset.screen;
    }
    if (tab.dataset.screen === "grafana") {
      const frame = $("#grafana-frame");
      // 指向 PurpleScope SOC dashboard 本身，不是 Grafana 首頁——首頁對玩家沒有
      // 任何意義（Welcome 畫面），這個分頁的存在理由是看這場演練的遙測。
      // kiosk 模式收掉 Grafana 自己的側欄／頂欄：iframe 裡的玩家是 Viewer，
      // 那些導覽對他不但無用，還會讓他以為自己能操作 Grafana。
      if (!frame.src) frame.src = "/grafana/d/purplescope-soc/purplescope-soc?kiosk";
    }
  });
}

renderAlerts();
renderDetail();
renderActions();
poll(8, refresh);
