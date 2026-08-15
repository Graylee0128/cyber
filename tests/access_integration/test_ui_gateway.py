"""Product UI gateway 的跨容器實證（#75／#76／#26）。

這條測試存在的理由跟 `test_response_dispatch_hop.py` 是同一個：**UI 的權限邊界
不住在 Python 裡，住在 nginx 設定裡**，任何單元測試都碰不到它。

具體會斷在哪：`deploy/ui/default.conf.template` 少注入一個 token，那個身分的
每個請求都變 400；token 注入到錯的前綴，紅隊就拿得到教官的 clearance。兩種都是
設定錯誤，兩種都不會有任何 Python 例外 —— 只會靜默地把權限模型換掉。

所以這裡驗的是三件事，全部經真的 nginx、打真的後端容器：

1. token 真的被注入了（沒注入 → Range Core 回 400 missing bearer token）
2. 前綴真的決定身分（red 前綴打 instructor-only 端點 → 403，不是 204）
3. 新加的兩條端點真的在部署裡有出口（`/api/techniques`、`/battleboard`）
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("PURPLE_ACCESS_E2E") != "1",
        reason="access-plane compose profile is not running",
    ),
]

UI_URL = os.environ.get("PURPLE_UI_URL", "http://localhost:8090")


def _parse(raw: bytes):
    """靜態畫面回的是 HTML／CSS／JS，不是 JSON —— 這裡不能假設每個回應都是 JSON。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw.decode(errors="replace")}


def _get(path: str, method: str = "GET", payload: dict | None = None, cookie: str | None = None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if cookie is not None:
        headers["Cookie"] = cookie
    request = Request(UI_URL + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, _parse(response.read())
    except HTTPError as error:
        return error.code, _parse(error.read())


def _post(path: str, payload: dict | None):
    return _get(path, method="POST", payload=payload)


def _instructor_cookie() -> str:
    """登入教官並回傳 session cookie（#126 item 2）。

    憑證就是部署注入的 `ADMISSION_INSTRUCTOR_TOKEN`，compose 裡設為
    `e2e-service-token`——不是測試專用的第二把鑰匙。
    """
    request = Request(
        UI_URL + "/gw/instructor/admission/instructor/login",
        data=json.dumps({"token": "e2e-service-token"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        assert response.status == 204, response.status
        set_cookie = response.headers.get("Set-Cookie")
    assert set_cookie, "登入沒有發出 session cookie"
    return set_cookie.split(";", 1)[0]


def test_public_static_screens_are_served():
    """公開層畫面都真的在 image 裡。少 build 一個目錄會在這裡變紅。"""
    for path in (
        "/index.html",
        "/battleboard/index.html",
        "/player/index.html",
        "/player/blue.html",
        "/blue-soc/index.html",
        "/instructor-login/index.html",
        "/assets/base.css",
        "/assets/api.js",
    ):
        status, body = _get(path)
        assert status == 200, f"{path} 沒有被服務出來：{body}"


def test_privileged_screens_require_an_instructor_session():
    """#126 item 2：教官／紫隊／中控三個畫面在登入前拿不到。

    這同時驗兩件事：檔案真的在 image 裡（登入後 200），以及 nginx 的
    `auth_request` 真的接上了（登入前 403）。少了後者，拿到網址就進得去。
    """
    privileged = (
        "/purple/index.html",
        "/instructor/index.html",
        "/event-control/index.html",
    )
    for path in privileged:
        status, _ = _get(path)
        assert status == 403, f"{path} 在沒有教官 session 時就被服務出來了"

    cookie = _instructor_cookie()
    for path in privileged:
        status, body = _get(path, cookie=cookie)
        assert status == 200, f"{path} 登入後仍拿不到：{body}"


def test_gateway_injects_the_service_token():
    """瀏覽器沒帶 Authorization，Range Core 卻認得這個呼叫者。

    唯一可能是 nginx 在 server 端把 token 貼上去了。這條若變成 400
    （missing Authorization bearer token），代表那一段注入掉了。
    """
    status, body = _get("/gw/red/core/api/scenarios")
    assert status == 200, body


def test_the_prefix_decides_the_identity_not_the_caller():
    """紅隊前綴打不到教官專屬的端點。

    `POST /api/exercises/reset` 需要 instructor clearance。經 red gateway 進來
    只會拿到 red 的 token，所以 Range Core 回 403。這條若變成 204，代表 token
    表貼錯前綴 —— 任何紅隊玩家都能把進行中的演練清掉。
    """
    status, body = _get("/gw/red/core/api/exercises/reset", method="POST")
    assert status == 403, body


def test_grafana_health_passthrough_has_no_identity_gate():
    """#126：Grafana 是唯一 alert engine，掛了偵測全停不會有信號。這條只補
    可見性，不補告警管線——Instructor Console 直接 fetch 這條顯示狀態燈，
    所以它必須對任何呼叫者都開放（沒有 gateway 前綴，不需要身分）。

    **不斷言 200**：`admission-e2e` 這個 profile 不含 grafana（它住預設
    profile），所以這裡正常會是 502 —— 而 502 本身就證明了要驗的那件事：
    請求穿過 nginx 走到了上游，沒有被身分檢查擋下來。斷言 200 會讓這條測試
    變成「grafana 有沒有在跑」的健康檢查，那不是它的職責。
    """
    status, _ = _get("/health/grafana")
    assert status not in (401, 403), "liveness passthrough 不該有身分檢查"
    assert status in (200, 502), status


def test_instructor_prefix_reaches_instructor_only_endpoints():
    """反面：同一條端點經 instructor 前綴就過得了 clearance 這一關。

    沒有進行中的演練時是 404（`no running exercise`），有的話是 204。
    兩者都代表**通過了身分檢查** —— 這條測試要證的就只有這件事，
    所以不預設演練狀態，403 才是失敗。
    """
    status, body = _get("/gw/instructor/core/api/exercises/reset", method="POST")
    assert status in (204, 404), body


def test_evaluation_api_has_a_deployment_exit():
    """`/api/techniques` 帶回判讀限制。

    Console 的 acceptance criteria 要求每個 technique 顯示判讀限制，而那段文字
    在 `config/techniques.yaml`。這條測試同時證明 Evaluation API 真的被部署了
    —— 在 `evaluation-api` service 出現以前，它一個容器都沒有。
    """
    status, body = _get("/gw/purple/eval/api/techniques")
    assert status == 200, body
    by_id = {technique["id"]: technique for technique in body}
    assert "T1190" in by_id
    assert by_id["T1005"]["note"], "T1005 的判讀限制不該是空的"


def test_copilot_summary_route_only_exists_for_instructor():
    """#133：SOC Copilot 這條路徑本身只在 instructor 前綴下存在。

    不是「其他身分打了會 403」——是其他前綴**沒有這條路由**（nginx 沒有對應
    location，直接落到靜態檔案處理變 404），跟 evidence／eval 那種「路由都在，
    靠 clearance 分級」的模式刻意不同：這條端點存不存在本身就敏感（洩漏
    「教官在看哪些玩家」），所以連 blue／purple 前綴都不該摸到它。

    instructor 前綴打得通，回應要有 `summary` key（AI 服務可能沒起，值可能
    是 null——這條只驗路由跟回應形狀，不驗 AI 真的有算出東西，那件事已經在
    2026-08-15 的手動 VM smoke test 驗過）。
    """
    status, body = _post("/gw/instructor/copilot/summary", {"player_statuses": []})
    assert status == 200, body
    assert "summary" in body

    for identity in ("blue", "purple", "red"):
        status, _ = _post(f"/gw/{identity}/copilot/summary", {"player_statuses": []})
        assert status == 404, f"{identity} 前綴不該有這條路由，卻回了 {status}"


def test_battleboard_projection_never_leaks_the_real_technique():
    """公開層前綴拿到的投影裡沒有任何 MITRE 編號。

    先真的種一份 registry 並凍結 —— 拿一個不存在的 exercise 去問只會拿到 404，
    那種測試永遠是綠的，也永遠證明不了「有資料時不洩題」。

    `admission-e2e` 這個 scenario 的 attack_chain 帶著 T1190，所以只要投影裡
    出現任何 `T1` 開頭的字串，就是那個編號漏出去了。
    """
    exercise_id = "ui-battleboard-probe"
    seed_status, seed_body = _post(
        f"/gw/purple/eval/api/exercises/{exercise_id}/actions",
        {"scenario_id": "admission-e2e"},
    )
    # 409 = 這場已經種過（同一個 job 重跑）。兩者都代表 registry 存在。
    assert seed_status in (201, 409), seed_body
    freeze_status, freeze_body = _post(
        f"/gw/purple/eval/api/exercises/{exercise_id}/actions/freeze", None
    )
    assert freeze_status in (200, 409), freeze_body

    status, body = _get(f"/gw/red/eval/api/exercises/{exercise_id}/battleboard")
    # 503 有兩個可能原因，都是已知範圍限制而非投影邏輯壞掉：
    #   1. 這個 profile 沒有 Loki，evaluation 拿不到遙測後端
    #   2. `config/scenario-sources.yaml` 的 `scenarios:` 清單刻意留空
    #      （WS2 spec §6.2），`admission-e2e` 還沒登記——見 ui/README.md 已知缺口 #6
    assert status in (200, 503), body
    if status != 200:
        pytest.skip(f"evaluation 回 503（已知缺口，見 ui/README.md）：{body}")

    assert body["revealed"] is False
    serialized = json.dumps(body)
    assert "T1" not in serialized, f"公開投影洩漏了技法編號：{serialized}"
    assert all(event["attack_label"].startswith("Attack #") for event in body["events"]), body
    assert all(event["disclosure"] == "pending" for event in body["events"]), body
