/* Event Control —— WS8 會議中控。
 *
 * 這一頁全部打 Admission API，token 一樣由 gateway 注入（Admission 的 instructor
 * token 與 Range Core 的是**不同命名空間**：一邊外洩不會換出另一邊的身分）。
 *
 * 座位表沒有 JSON 端點 —— Admission 只有一個伺服器端渲染的
 * `/admission/instructor/{id}/console`（Jinja2）。與其在這裡再刻一份會漂掉的
 * 座位表，不如把那頁連出去；重複兩份座位狀態就是兩份會對不起來的真相。
 */

import { Gateway, humanize, $, el, clear, renderEmpty, showBanner } from "../assets/api.js";

const api = new Gateway("instructor");
const banner = $("#banner");

const state = { exerciseId: null, links: [] };

function requireExercise() {
  if (!state.exerciseId) {
    showBanner(banner, "先填入 exercise_id 並按載入。", "info");
    return false;
  }
  return true;
}

/* ---------- 載入 ---------- */

$("#btn-load").addEventListener("click", async () => {
  const exerciseId = $("#exercise-id").value.trim();
  if (!exerciseId) return;
  state.exerciseId = exerciseId;
  clear($("#seat-table-link")).append(el("a", {
    href: `/gw/instructor/admission/instructor/${encodeURIComponent(exerciseId)}/console`,
    target: "_blank",
    text: "開啟伺服器端渲染的完整座位表 →",
  }));
  await Promise.all([loadAvailability(), loadAlerts()]);
});

async function loadAvailability() {
  const host = clear($("#availability"));
  try {
    const availability = await api.admission(`/${encodeURIComponent(state.exerciseId)}/availability`);
    showBanner(banner, "");
    for (const [key, value] of Object.entries(availability)) {
      host.append(el("div", { class: "kv-row" }, [
        el("span", { class: "k", text: key }),
        el("span", { text: String(value) }),
      ]));
    }
    if (typeof availability.red_cap === "number") $("#red-cap").value = availability.red_cap;
    if (typeof availability.blue_cap === "number") $("#blue-cap").value = availability.blue_cap;
  } catch (error) {
    renderEmpty(host, error.status === 404
      ? "這場演練還沒有座位池設定 —— 設定上限就會建立。"
      : humanize(error));
  }
}

async function loadAlerts() {
  const host = clear($("#alerts"));
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
    renderEmpty(host, humanize(error));
  }
}

/* ---------- 座位池 ---------- */

$("#btn-set-caps").addEventListener("click", async () => {
  if (!requireExercise()) return;
  try {
    await api.admission(`/${encodeURIComponent(state.exerciseId)}/pool-config`, {
      method: "PUT",
      body: {
        red_cap: Number($("#red-cap").value),
        blue_cap: Number($("#blue-cap").value),
      },
    });
    showBanner(banner, "上限已設定。", "info");
    loadAvailability();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

$("#btn-lock").addEventListener("click", async () => {
  if (!requireExercise()) return;
  if (!window.confirm(
    "鎖定座位池並建置藍隊座位。這一步不可逆 —— 建置之後上限不再是可調參數。確定嗎？"
  )) return;
  try {
    await api.admission(`/${encodeURIComponent(state.exerciseId)}/pool-config/lock`, { method: "POST" });
    showBanner(banner, "座位池已鎖定，藍隊座位已建置。", "info");
    loadAvailability();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

/* ---------- 遠端連結 ---------- */

$("#btn-issue-link").addEventListener("click", async () => {
  if (!requireExercise()) return;
  try {
    const link = await api.admission(`/${encodeURIComponent(state.exerciseId)}/remote-links`, {
      method: "POST",
    });
    state.links.unshift(link);
    renderLinks();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

function renderLinks() {
  const host = clear($("#links"));
  if (state.links.length === 0) {
    renderEmpty(host, "尚未簽發任何連結。");
    return;
  }
  for (const link of state.links) {
    const block = el("div", { style: "margin-top:12px" }, [
      el("div", { class: "row" }, [
        el("span", { class: "k mono", text: link.link_id }),
        el("button", {
          class: "danger",
          text: "撤銷",
          onclick: () => revokeLink(link.link_id),
        }),
      ]),
      el("div", { class: "token", text: link.token }),
    ]);
    host.append(block);
  }
}

async function revokeLink(linkId) {
  try {
    await api.admission(`/remote-links/${encodeURIComponent(linkId)}`, { method: "DELETE" });
    state.links = state.links.filter((link) => link.link_id !== linkId);
    renderLinks();
    showBanner(banner, "連結已撤銷。", "info");
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

/* ---------- 維運與單一座位 ---------- */

$("#btn-expire").addEventListener("click", async () => {
  try {
    const result = await api.admission("/maintenance/expire", { method: "POST" });
    clear($("#expire-result")).append(el("div", { class: "kv-row" }, [
      el("span", { class: "k", text: "已清理" }),
      el("span", { text: JSON.stringify(result) }),
    ]));
    loadAlerts();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
});

async function seatAction(path, label, confirmText) {
  const seatId = $("#seat-id").value.trim();
  if (!seatId) {
    showBanner(banner, "先填入 seat_id。", "info");
    return;
  }
  if (confirmText && !window.confirm(confirmText)) return;
  try {
    await api.admission(`/seats/${encodeURIComponent(seatId)}${path}`, { method: "POST" });
    showBanner(banner, `${label}完成。`, "info");
    loadAvailability();
    loadAlerts();
  } catch (error) {
    showBanner(banner, humanize(error));
  }
}

$("#btn-rebind").addEventListener("click", () => seatAction("/rebind", "重新綁定"));
$("#btn-release").addEventListener("click", () =>
  seatAction("/release", "釋出", "釋出這個座位？佔用它的玩家會失去會話。"));

renderLinks();
