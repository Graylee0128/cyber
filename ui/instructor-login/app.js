/* Instructor Login —— #126 item 2。
 *
 * 換 session cookie 的唯一入口。刻意是獨立畫面而不是嵌在 Instructor Console
 * 裡：這一頁必須落在 nginx 的 auth_request 保護範圍**之外**（見
 * deploy/ui/default.conf.template），否則登入永遠拿不到能通過那層檢查的
 * cookie——嵌在受保護畫面裡就沒有這個保證。
 */

import { Gateway, humanize, $, showBanner } from "../assets/api.js";

const api = new Gateway("instructor");
const banner = $("#banner");
const input = $("#token");
const button = $("#submit");

async function login() {
  const token = input.value.trim();
  if (!token) {
    showBanner(banner, "請輸入 token。", "info");
    return;
  }
  button.disabled = true;
  try {
    await api.admission("/instructor/login", { method: "POST", body: { token } });
    location.href = "../instructor/";
  } catch (error) {
    showBanner(banner, error.status === 403 ? "Token 錯誤。" : humanize(error));
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", login);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter") login();
});
