/* 前端與後端之間唯一的一層。
 *
 * ## 為什麼所有請求都走 `/gw/<identity>/...`
 *
 * Range Core 與 Evidence API 的身分都由**部署時注入的服務 token**換出
 * （`disclosure.identity`，票 #52 B2）。那代表 token 一旦交給瀏覽器，
 * 「呼叫者無法自報身分」這個保證就當場失效 —— 紅隊玩家打開 devtools 就能
 * 拿到 instructor token 去 `POST /api/exercises/reset`，把整場演練清掉。
 *
 * 所以這裡**沒有任何地方拿得到 token**。每個身分有自己的 gateway 前綴，
 * token 由 nginx 在 server 端注入（見 `deploy/ui/nginx.conf`）。瀏覽器送出去的
 * 是不帶 Authorization 的請求，前綴決定它會被貼上哪個身分。
 *
 * 遮蔽同理：clearance 由 gateway 前綴對應的 identity 決定，遮蔽發生在後端組裝
 * 回應時，不是前端渲染時 —— 前端遮是假的（`purple/evidence/service.py`
 * `render_bundle` 的 docstring 講的是同一件事）。
 */

const IDENTITIES = ["red", "blue", "purple", "instructor"];

export class ApiError extends Error {
  constructor(status, detail, { path } = {}) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
}

/** 把後端的錯誤翻成畫面上敢直接顯示的一句話。
 *
 * 使用者看到的是友善訊息，完整內容進 console —— 全域 security 規則的
 * 「錯誤訊息不外洩內部細節、詳細脈絡留在日誌」在前端的對應做法。 */
export function humanize(error) {
  if (!(error instanceof ApiError)) return "連線失敗，請確認服務是否啟動。";
  switch (error.status) {
    case 400: return "請求缺少必要欄位。";
    case 401:
    case 403:
      // 這條路徑上最常見的 403 不是「權限不足」而是來源 IP 不在名冊上，
      // 兩者的處置完全不同（一個要換身分，一個要改部署），所以分開講。
      return error.detail?.includes("roster")
        ? "這台機器的來源 IP 不在本場演練名冊上（見 ui/README.md 的來源 IP 歸屬一節）。"
        : "這個身分沒有權限執行這個動作。";
    case 404: return "找不到對應資料（可能是還沒有進行中的演練）。";
    case 409: return error.detail || "目前的狀態不允許這個動作。";
    case 422: return error.detail || "輸入的內容不合法。";
    case 503: return "後端服務尚未就緒（遙測後端或必要設定缺席）。";
    default:  return `後端回應 ${error.status}。`;
  }
}

async function request(path, { method = "GET", body } = {}) {
  const init = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    console.error("network failure", path, cause);
    throw new ApiError(0, "network failure", { path });
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text };
    }
  }

  if (!response.ok) {
    const detail = typeof payload?.detail === "string"
      ? payload.detail
      : (payload?.error ?? JSON.stringify(payload?.detail ?? payload ?? ""));
    console.error("api error", response.status, path, detail);
    throw new ApiError(response.status, detail, { path });
  }
  return payload;
}

/** 一個身分能打的所有後端。同一頁只該有一個 Gateway 實例。 */
export class Gateway {
  constructor(identity) {
    if (!IDENTITIES.includes(identity)) {
      throw new Error(`unknown identity: ${identity}`);
    }
    this.identity = identity;
    this.base = `/gw/${identity}`;
  }

  /** Range Core（Z-APP）。`path` 從 `/api/` 開始。 */
  core(path, options) { return request(`${this.base}/core${path}`, options); }

  /** Evaluation Engine（Z-MGMT）。 */
  evaluation(path, options) { return request(`${this.base}/eval${path}`, options); }

  /** Evidence API（Z-MGMT）。回傳的欄位已依 clearance 遮蔽過。 */
  evidence(eventId) { return request(`${this.base}/evidence/${encodeURIComponent(eventId)}`); }

  /** Admission（僅 instructor gateway 有這條）。 */
  admission(path, options) { return request(`${this.base}/admission${path}`, options); }

  /** Core Event 的 SSE 串流。
   *
   * `EventSource` 不能自訂 header，所以 `Last-Event-ID` 的續傳完全靠瀏覽器
   * 自己在重連時送回上一個 `id:` —— 這正是 Range Core 那端設計成用
   * `core_events.seq` 當 id 的原因（#36），前端不需要、也不應該自己記游標。
   *
   * 連線壽命上限 300 秒是後端刻意的（`DEFAULT_MAX_STREAM_SECONDS`），
   * 到期關閉不是錯誤，瀏覽器會自動帶著 Last-Event-ID 重連。所以 `onerror`
   * 只更新指示燈，不彈錯誤、不自己重連 —— 自己重連會跟瀏覽器打架。 */
  stream({ onEvent, onOpen, onDrop }) {
    const source = new EventSource(`${this.base}/core/api/events/live`);
    source.onopen = () => onOpen?.();
    source.onerror = () => onDrop?.();
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data), Number(message.lastEventId));
      } catch (cause) {
        console.error("unparsable SSE frame", message.data, cause);
      }
    };
    return source;
  }
}

/* ---------- 小工具（畫面共用，沒有商業邏輯）---------- */

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** 一律用 textContent 建節點，不拼 innerHTML —— 事件內容含攻擊者可控字串
 *  （service、source_ip、rule 標題），拼字串等於把 XSS 種進自己的 SOC 畫面。 */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/** 空狀態一律說明「為什麼空」，不留白 —— 空白會被讀成「藍隊什麼都沒看到」，
 *  那是 evaluation API 用 503 而不是 200＋空資料在防的同一種誤讀。 */
export function renderEmpty(node, reason) {
  clear(node).append(el("div", { class: "empty", text: reason }));
}

export function showBanner(node, message, kind = "error") {
  node.className = `banner ${kind}`;
  node.textContent = message ?? "";
}

export const clockTime = (iso) => {
  if (!iso) return "—";
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? "—" : at.toLocaleTimeString("zh-Hant", { hour12: false });
};

export function countdown(endsAt) {
  const remaining = Math.max(0, new Date(endsAt).getTime() - Date.now());
  const total = Math.floor(remaining / 1000);
  const mm = String(Math.floor(total / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** 每 `seconds` 秒跑一次，並且**立刻先跑一次**。回傳停止函式。 */
export function poll(seconds, task) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      await task();
    } catch (cause) {
      console.error("poll task failed", cause);
    }
  };
  tick();
  const handle = setInterval(tick, seconds * 1000);
  return () => { stopped = true; clearInterval(handle); };
}
