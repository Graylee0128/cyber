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
  await Promise.all([loadAvailability(), loadAlerts(), warnIfExerciseIdMismatchesRangeCore(exerciseId)]);
});

/* ---------- #143 item 5：這裡的 exercise_id 是手打的，跟 Range Core 沒有任何關聯 ----------
 * 座位池、遠端連結、告警全部照這個手打的 id 去查／改。教官忘記結束並重置舊場次、
 * 又在這裡打了一個對不上的 exercise_id 時，Admission 這邊完全沒有訊號——不會 404，
 * 因為 pool-config 是以這個 id 為 key 新建的，看起來一切正常，只是動的是空氣。
 * 這裡不擋任何操作（Event Control 本來就允許在演練開始前先設定座位池），只在
 * Range Core 已經有一場**不同** id 在跑時提出警告，教官才知道兩邊對不上。 */
async function warnIfExerciseIdMismatchesRangeCore(exerciseId) {
  let running;
  try {
    running = await api.core("/api/exercises/current");
  } catch {
    return; // Range Core 讀不到就不強加一個可能誤導的警告，loadAlerts 已經會反映連線問題。
  }
  if (running && running.exercise_id !== exerciseId) {
    showBanner(
      banner,
      `Range Core 目前正在跑的是 ${running.exercise_id}，跟這裡輸入的 ${exerciseId} 不一樣`
      + "——座位池與連結操作的是後者，不是正在跑的那一場。",
      "error",
    );
  }
}

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
    const children = [
      el("div", { class: "row" }, [
        el("span", { class: "k mono", text: link.link_id }),
        el("button", {
          class: "danger",
          text: "撤銷",
          onclick: () => revokeLink(link.link_id),
        }),
      ]),
    ];
    // `join_url` 只在 Admission 設了 ADMISSION_PUBLIC_BASE_URL 時才有值
    // （#143 item 3）——沒設就只印裸 token，跟修這個之前的行為一樣，不是壞掉。
    if (link.join_url) {
      children.push(el("a", {
        href: link.join_url, target: "_blank", class: "token", text: "可點的連結 →",
      }));
    } else {
      children.push(el("div", { class: "token", text: link.token }));
    }
    host.append(el("div", { style: "margin-top:12px" }, children));
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
