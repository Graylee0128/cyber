"""靶機 VM 的受攻擊面（Slice 4 / 票 #9；CH2/CH3/CH4/FINAL 見 #153）—— 跑在 Z-TARGET(VLAN20) 的真 VM 內。

與 compose 的 vulnerable-app 不同：這支是給**紅隊容器隔著真 VLAN 打**的，而偵測靠
同一台 VM 內的 Falco（modern-eBPF）看 syscall，不是靠 app 自己判斷。

端點：
- `/exec`         生一個帶 PURPLESCOPE_EXEC 標記的 shell → Falco 抓 execve → T1059
- `/readsecret`   讀 /etc/purplescope/secret.txt        → Falco 抓 open   → T1005（SA §7 Scenario 03）
- `/healthz`      存活探測
- `/poster/upload` `/poster/render`  海報上傳→自訂範本執行（CH2 計分攻擊面，見下方段落）
- `/preview` `/internal/reports`     網址預覽 SSRF →竊取內部憑證→ API pivot（CH3，見下方段落）
- `/diagnostics/lookup`              報修診斷工具指令注入 → cron 持久化（CH4，見下方段落）
- `/students/token` `/students/<id>/records`  自助登入 token → IDOR → 批次外洩（FINAL，見下方段落）

每個請求寫一行 JSON 到 app log（含 source_ip）。Alloy 把 app log 與 Falco events.json
一起推到 Z-MGMT 的 Loki —— 那條 TARGET→MGMT :3100 就是契約 1 的實用。
app log 裡的 source_ip 也是「六台紅隊 source IP 可分辨」在真環境的觀測點。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOG_PATH = os.environ.get("TARGET_LOG_PATH", "/var/log/range-target/app.log")
SECRET_PATH = os.environ.get("TARGET_SECRET_PATH", "/etc/purplescope/secret.txt")
PORT = int(os.environ.get("TARGET_PORT", "80"))
HEARTBEAT_INTERVAL_S = 30

# ── 計分攻擊面（票 #44）——「真的要打進去」的 SQLi，與上面的 fixture 端點分屬二分兩側。
#
# `/product?id=<raw>` 把使用者輸入**直接字串串接**進 SQL，不做參數化 —— 這是刻意的、
# 也是可計分攻擊面「必須真的可利用」的落地（spec §3.1）。紅隊用 UNION 從這裡撈出
# credentials 表裡的 dbadmin 帳密（第一關產出），再拿去直連 :3306 讀 vault.flag（第二關）。
#
# app 用 webapp@localhost 連 DB —— 這個帳號對 vault 沒有 grant，所以**光靠這條 SQLi
# 撈不到 flag**（seed.sql 的授權邊界）。這就是「未經利用拿不到 flag」與「內容鏈接」。
DB_HOST = os.environ.get("TARGET_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("TARGET_DB_PORT", "3306"))
DB_USER = os.environ.get("TARGET_DB_USER", "webapp")
DB_PASSWORD = os.environ.get("TARGET_DB_PASSWORD", "webapp-local-only")
DB_NAME = os.environ.get("TARGET_DB_NAME", "shopdb")
# webapp 是 socket-only 帳號（seed.sql 只建 webapp@localhost）—— 刻意如此：webapp 不對
# VLAN30 的 TCP 開放，只有 dbadmin@'%' 是 TCP 可達的第二道門。所以本機連線必須走 unix
# socket；用 TCP 連 127.0.0.1 會被認成 webapp@127.0.0.1 而拒絕（實測 1045 access denied）。
DB_SOCKET = os.environ.get("TARGET_DB_SOCKET", "/run/mysqld/mysqld.sock")
_DB_IS_LOCAL = DB_HOST in ("localhost", "127.0.0.1", "::1")


# /product 的 SQLi 偵測標記。app 只**記錄**這個布林，不據以擋 —— 攻擊面刻意可利用
# （票 #44）。偵測交給 Grafana rule 數 sqli_suspected=true 的筆數（與 compose
# vulnerable-app 同款，見 deploy/grafana rules SQLInjectionBurstTarget）。
SQLI_MARKERS = ("' or ", " or 1=1", "union select", "-- ", "'--")


def _looks_like_sqli(value: str) -> bool:
    low = value.lower()
    return any(m in low for m in SQLI_MARKERS)


def build_product_query(raw_id: str) -> str:
    """把 id 直接串進 SQL —— 真 SQLi，不是模擬。

    抽成 module-level 純函式的唯一理由：可注入性要**被測試接住**而不必連真 DB。
    `tests/deploy/test_target_attack_surface.py` 餵一個 UNION payload，斷言它原封不動
    出現在回傳的 SQL 裡（＝沒有任何 escaping／參數化）。若哪天有人把這裡改成參數化查詢，
    那條測試會紅 —— 提醒他「這是計分攻擊面，不能修」。
    """
    return f"SELECT id, name, price, description FROM products WHERE id = {raw_id}"


def _query_products(raw_id: str) -> list[tuple]:
    """以 webapp@localhost 執行可注入的 query。pymysql 延後 import：DB 掛了也不影響
    fixture 端點（那些完全不碰 DB），import 失敗也只讓計分面回 500，不拖垮整支 app。"""
    import pymysql  # noqa: PLC0415 — 刻意延後：只有計分面用得到，fixture 不必背這個相依

    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER,
        password=DB_PASSWORD, database=DB_NAME, connect_timeout=5,
        unix_socket=DB_SOCKET if _DB_IS_LOCAL else None,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(build_product_query(raw_id))
            return list(cur.fetchall())
    finally:
        conn.close()


# ── CH2 Foothold（校園海報上傳，#153 Campaign Pack v1）——「真的要打進去」的第二個計分
# 攻擊面，與上面的 SQLi 分屬不同 surface：這裡打的是檔案上傳信任鏈，不是 SQL 拼接。
#
# 平台把上傳限制做成「只查 Content-Type header」——這個 header 是**攻擊者自報**的，
# 伺服器完全沒有驗證副檔名或檔案內容（magic bytes）。攻擊者送 `Content-Type: image/png`
# 就能把任何內容（含 .py）放進 POSTER_DIR，繞過形同虛設。
#
# 光是放上去還不夠：`/poster/render` 才是真正的執行點。它支援「進階自訂範本」——
# 檔名以 .py 結尾的海報會被當成範本腳本，直接 `python3 <file>` 執行。這就是
# Upload bypass（T1190）→ Web Shell（T1505）的落地：攻擊者用一次上傳＋一次 render
# 請求就拿到程式碼執行。
POSTER_DIR = os.environ.get("TARGET_POSTER_DIR", "/var/lib/purplescope/posters")
# render 子行程改丟給這個低權限帳號執行（preexec_fn setuid/setgid）——即使模板真的被
# 執行，也不該直接是 range-target-app 本身的 root。golden VM 由 bake.sh 建立這個帳號；
# 本機測試/CI 沒有這個系統帳號時 `_drop_privileges_to_posterrender()` 回 None，子行程
# 沿用呼叫者權限，讓「上傳→執行」這段邏輯不因缺帳號被整段跳過（見 tests/deploy）。
POSTER_RENDER_USER = os.environ.get("TARGET_POSTER_RENDER_USER", "posterrender")
ALLOWED_POSTER_CONTENT_TYPES = ("image/png", "image/jpeg", "image/gif")


def _poster_upload_bypasses_content_check(content_type: str, filename: str) -> bool:
    """True＝這次上傳會被現行檢查放行，但其實是可執行內容 —— 繞過成立。

    現行檢查只認 Content-Type 落在允許清單就放行，不管副檔名、不開檔驗證 magic
    bytes。純函式抽出來是同一個理由（見 `_looks_like_sqli`）：漏洞的判定邏輯要被
    測試釘住，不能只活在 handler 裡靠人工檢查。若哪天有人「順手」把檢查改成同時
    驗證副檔名或內容，這條測試會紅，逼他面對「這就是要拿掉的漏洞」。
    """
    passes_check = content_type in ALLOWED_POSTER_CONTENT_TYPES
    is_executable_template = filename.lower().endswith(".py")
    return passes_check and is_executable_template


def _is_safe_poster_name(name: str) -> bool:
    """檔名必須是單一檔名成分，不含路徑分隔字元——這裡刻意擋掉的是路徑穿越，
    不是本章要展示的漏洞（那是 content-type/副檔名信任鏈），兩者不該混在一起。"""
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name


def _drop_privileges_to_posterrender():
    """回傳一個 preexec_fn，讓 render 子行程改以 POSTER_RENDER_USER 身分執行。

    找不到這個系統帳號（本機開發機／CI，golden VM 尚未烤過）就回 None，交由呼叫者
    自行決定要不要仍然執行子行程——這讓「上傳→執行」這段邏輯不必依賴真 VM 也能被
    測試覆蓋；真正的權限邊界（sudoers 誤設放行 root，見 `deploy/range-target/RUNBOOK
    -attack-chain.md` CH2 段）只能在大主機 golden VM 上驗證（T4）。
    """
    try:
        import pwd  # noqa: PLC0415 — 只有真的要 setuid 時才需要，且 Windows 開發機沒有這個模組
        pw = pwd.getpwnam(POSTER_RENDER_USER)
    except (KeyError, ImportError):
        return None

    def _preexec() -> None:
        os.setgid(pw.pw_gid)
        os.setuid(pw.pw_uid)

    return _preexec


# ── CH3 The Stolen Key（校園網址預覽 SSRF，#153 Campaign Pack v1）—— 第三個計分
# 攻擊面。跟 CH1（DB）、CH2（檔案上傳）都不同：這裡打的是「伺服器替你發 outbound
# 請求」這個信任邊界。
#
# `/preview` 是「幫你抓網址預覽」的功能：接受任意使用者提供的 URL，伺服器端直接
# fetch 回傳內容 —— 完全沒有過濾目的地是不是內部位址（真 SSRF，不是模擬）。
#
# 靶機另外在 **127.0.0.1 專屬 port**（METADATA_PORT）跑一個小 HTTP 服務，模擬雲端
# 常見的 instance metadata endpoint（IMDS 形狀）。這個 port 只 bind 在 loopback——
# 外部網路連不到它，唯一能碰到它的是**靶機自己**。這正是 SSRF 之所以危險的原因：
# 攻擊者透過 /preview 借伺服器的手，讓伺服器自己去打只有自己碰得到的位址。
#
# 讀到憑證後，`/internal/reports` 是一個「原本要授權才能進」的內部 API——帶對
# 憑證的 `X-Internal-Token` 才放行，模擬「拿竊得的雲端憑證橫向 pivot 到別的內部
# 服務」（T1550）。這一步在 CH3 v1 **沒有**掛計分 objective（見下方 §3 平台限制的
# 說明），純粹是攻擊鏈敘事上的第三步，供 Purple 側看見完整故事。
METADATA_PORT = int(os.environ.get("TARGET_METADATA_PORT", "8169"))
METADATA_ROLE_NAME = os.environ.get("TARGET_METADATA_ROLE", "campus-portal-role")
INTERNAL_API_TOKEN = os.environ.get("TARGET_INTERNAL_TOKEN", "campus-internal-9f2b7d")

# 判定「這個 URL 看起來像 SSRF」的字串標記（同 SQLI_MARKERS 的作法：純函式抽出來，
# 供測試釘住，不能只活在 handler 裡）。命中任一個代表目的地不是一般公開網址，
# 而是內部／loopback／連結本地位址 —— 一般網址預覽功能不該碰得到這些。
SSRF_MARKERS = ("127.0.0.1", "localhost", "169.254.", "10.", "172.16.", "172.17.",
                "172.18.", "172.19.", "172.2", "172.30.", "172.31.", "192.168.",
                f":{METADATA_PORT}")


def _looks_like_ssrf(url: str) -> bool:
    low = url.lower()
    return any(m in low for m in SSRF_MARKERS)


def _fetch_preview(url: str) -> tuple[int, bytes]:
    """把 url 原封不動丟給 urllib 抓——沒有目的地白名單/黑名單，這正是漏洞本身。

    抽成純函式的理由同 `build_product_query`：可利用性要被測試接住，不必連真的
    metadata service。timeout 短是避免 SSRF 到不存在的位址把整個 handler 卡死；
    這是穩定性考量，不是漏洞的一部分。
    """
    req = urllib.request.Request(url, headers={"User-Agent": "campus-link-preview/1.0"})
    with urllib.request.urlopen(req, timeout=3) as resp:  # noqa: S310 — SSRF 是本章的計分攻擊面
        return resp.status, resp.read()


class _MetadataHandler(BaseHTTPRequestHandler):
    """只 bind 127.0.0.1：外部網路連不到，唯一入口是靶機自己（也就是被 SSRF 借用）。"""

    def do_GET(self) -> None:
        if self.path == "/latest/meta-data/iam/security-credentials/":
            self._text(200, METADATA_ROLE_NAME + "\n")
            return
        if self.path == f"/latest/meta-data/iam/security-credentials/{METADATA_ROLE_NAME}":
            body = json.dumps({
                "Code": "Success",
                "Type": "AWS-HMAC",
                "AccessKeyId": "CAMPUSEXERCISEONLY",
                "SecretAccessKey": INTERNAL_API_TOKEN,
                "Token": INTERNAL_API_TOKEN,
            })
            self._text(200, body)
            return
        self._text(404, "not found\n")

    def _text(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def _start_metadata_service() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", METADATA_PORT), _MetadataHandler)
    server.serve_forever()


# ── CH4 Ghost in the System（校園報修診斷工具，#153 Campaign Pack v1）—— 第四個計分
# 攻擊面。跟前三章都不同：這裡打的是「使用者輸入直接進 shell 指令字串」這個經典
# 信任邊界，而且**會留下持久化狀態**（不像 CH1/CH3 唯讀）。
#
# `/diagnostics/lookup` 是「檢查這個主機名有沒有異常」的報修小工具：伺服器端組出
# `sh -c "echo checking <host>"` 直接執行。`<host>` 完全沒有跳脫或白名單——正常
# 使用（純主機名）時 sh 只會跑內建的 echo，不會 fork 出任何子行程；一旦帶 shell
# 特殊字元（`;` `|` `` ` `` `$(` 等）斷句，sh 就會把後面的內容當成**新指令**執行，
# 這就是指令注入成立那一刻（T1059）。
#
# 拿到指令執行後，攻擊者可以用同一個注入點把持久化寫進 cron.d（T1053）：讓一個
# 排程任務在原本的 session 結束後依然會被觸發。CH4 v1 只驗證「持久化檔案被寫入」
# 這個可觀測狀態，不驗證 cron daemon 真的在之後某分鐘觸發它（那需要真等一分鐘，
# 且與此章的偵測/計分邏輯無關——藍隊要看見的是「排程被動過」這個動作本身）。
DIAG_SHELL = os.environ.get("TARGET_SHELL", "/bin/sh")
CRON_FILE = os.environ.get("TARGET_CRON_FILE", "/etc/cron.d/campus-report")

# 判定「這個輸入看起來像指令注入」的字串標記（同 SQLI_MARKERS／SSRF_MARKERS 的
# 作法）。命中任一個代表這不是一個單純主機名，而是想斷句塞進新指令。
CMD_INJECTION_MARKERS = (";", "|", "&", "$(", "`", "\n", "||", "&&")


def _looks_like_command_injection(raw: str) -> bool:
    return any(m in raw for m in CMD_INJECTION_MARKERS)


def _run_diagnostic(raw_host: str) -> tuple[int, str]:
    """把 raw_host 原封不動組進 shell 指令字串——沒有跳脫、沒有參數化，這正是
    漏洞本身。抽成純函式的理由同 `build_product_query`：可利用性要被測試接住。
    """
    result = subprocess.run(
        [DIAG_SHELL, "-c", f"echo checking {raw_host}"],
        capture_output=True, timeout=5, check=False, text=True,
    )
    return result.returncode, (result.stdout + result.stderr)


# ── FINAL The Leak（校園學生資料自助查詢，#153 Campaign Pack v1）—— 第五個計分
# 攻擊面，也是刻意的 detection-gap 教學案例。跟前四章都不同：這裡沒有 payload、
# 沒有異常 exec、沒有異常 egress，只有一連串「看起來完全正常」的已認證請求。
#
# `/students/token?student_id=<raw>` 是自助登入：宣告自己是哪個學生，就核發一組
# token——**不驗證你是不是真的那個學生**（沒有密碼、沒有任何身分證明）。
# `/students/<id>/records?token=<t>` 只檢查 token 是不是「曾經核發過的某個 token」，
# **不檢查這個 token 原本是核發給誰的**——這就是 IDOR：認證存在（有沒有 token），
# 但物件層授權不存在（這筆資料是不是你的）。
#
# T1087（帳號列舉）：短時間內對 /students/token 打一連串不同 student_id，核發
# 一堆「合法但不屬於自己」的 token。這一步**有偵測覆蓋**（見 detection 段）。
# T1213／T1567（實際讀取＋批次外洩）：拿其中任一 token 打不同 id 的 /records，
# 一筆一筆讀走。這兩步**刻意零覆蓋**——遙測看得到（每次請求都寫進 app log），
# 但沒有任何 Grafana 規則會為它告警，因為這些請求在 log 裡長得跟正常使用一模
# 一樣（差別只在「這個 id 不是這個 token 原本核發的那個」，一個規則抓不到的
# 語意層事實）。
_token_lock = threading.Lock()
_ISSUED_TOKENS: dict[str, str] = {}  # token -> 核發時宣告的 student_id（只供稽核，不用來授權）


def _issue_student_token(claimed_id: str) -> str:
    token = secrets.token_hex(16)
    with _token_lock:
        _ISSUED_TOKENS[token] = claimed_id
    return token


def _token_is_valid(token: str) -> bool:
    """唯一的授權檢查：token 是不是「曾經核發過的某個」。不檢查跟哪個 id 綁定——
    這正是 IDOR 成立的原因，不是弱檢查被繞過，是這層檢查本身就不存在。"""
    with _token_lock:
        return token in _ISSUED_TOKENS


def _looks_like_idor_access(requested_id: str, token: str) -> bool:
    """純函式：這次讀取的 id 是不是跟 token 原本核發的 id 不同。只供**稽核 log**
    使用——app 仍然放行（漏洞的一部分），但至少把這個語意層事實寫進遙測，讓
    Purple 側能討論「看得到卻沒有規則能認出來」是什麼意思（ADR ③）。"""
    with _token_lock:
        issued_for = _ISSUED_TOKENS.get(token)
    return issued_for is not None and issued_for != requested_id


def _fake_student_record(student_id: str) -> dict:
    """合成資料，非真人資訊。用 id 做 deterministic 生成，讓同一個 id 每次讀到
    的內容一致（真實感），但整份資料表都是演練用的假值。

    用 `hashlib` 而非內建 `hash()`：後者對 str 預設有 per-process 隨機種子
    （PYTHONHASHSEED），同一個 id 換一次程序重啟就會讀到不同數字，「每次讀到的
    內容一致」這個宣稱就不成立了。`hashlib` 給的是真正跨程序穩定的雜湊。
    """
    digest = int(hashlib.sha256(student_id.encode()).hexdigest()[:4], 16)
    return {
        "student_id": student_id,
        "name": f"Student {student_id}",
        "email": f"{student_id}@minghu-demo.edu",
        "gpa": round(2.0 + (digest % 200) / 100, 2),
        "note": "PurpleScope 演練用合成資料，非真人資訊",
    }


_lock = threading.Lock()
_seq = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_log(entry: dict) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with _lock, open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _next_marker(kind: str) -> str:
    global _seq
    with _lock:
        _seq += 1
        return f"PURPLESCOPE_{kind}_{_seq}"


def emit_alloy_heartbeat() -> None:
    """End-to-end canary：只有 Alloy 還能轉送時，這筆才會出現在 Loki。"""
    _write_log({"ts": _now(), "app": "range-target", "event": "alloy.heartbeat"})


def _alloy_heartbeat_loop() -> None:
    while True:
        emit_alloy_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL_S)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        # source_ip 一律取 TCP 連線的對端，**絕不看 X-Forwarded-For**。
        # 這條拓樸裡紅隊直連靶機，中間沒有任何 proxy —— 信任 XFF 換不到好處，卻讓
        # 「六台紅隊 source IP 可分辨」這個契約證據變成送個 header 就能偽造的東西。
        # 歸屬證據不能由被歸屬方控制（2026-08-09 code review）。
        source_ip = self.client_address[0]

        if path == "/healthz":
            self._text(200, "ok")
            return

        if path == "/product":
            # 計分攻擊面（票 #44）：id 直接進 SQL，可 UNION 撈 credentials。
            # 回傳撈到的列，讓紅隊看得到戰利品；來源 IP 照樣入 log（歸屬證據）。
            raw_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            try:
                rows = _query_products(raw_id)
            except Exception as exc:  # noqa: BLE001 — SQL 錯誤原文回給紅隊是 SQLi 的一部分
                _write_log({"ts": _now(), "app": "range-target", "path": "/product",
                            "source_ip": source_ip, "id": raw_id, "outcome": "db_error",
                            "sqli_suspected": _looks_like_sqli(raw_id), "error": str(exc)})
                self._text(500, f"query error: {exc}\n")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/product",
                        "source_ip": source_ip, "id": raw_id, "outcome": "query",
                        "sqli_suspected": _looks_like_sqli(raw_id), "rows": len(rows)})
            self._text(200, json.dumps(rows, ensure_ascii=False, default=str) + "\n")
            return

        if path == "/uncovered":
            # 「有遙測、但沒有任何 Grafana 規則覆蓋」的動作 —— 決定性測試的真環境素材。
            # Falco 會抓到並推進 Loki（遙測在），但 deploy/grafana 那邊刻意沒有對應規則，
            # 所以不會有告警、不會有 Core Event。這正是 ADR ③ 要能分辨的
            # 「看得到卻沒偵測到」= DETECTION_GAP，而不是「根本沒看到」= VISIBILITY_GAP。
            marker = _next_marker("UNCOVERED")
            try:
                subprocess.run(
                    ["/bin/sh", "-c", f"echo {marker}; id"],
                    capture_output=True, timeout=5, check=False,
                )
            except Exception as exc:  # noqa: BLE001
                self._text(500, f"exec error: {exc}")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/uncovered",
                        "source_ip": source_ip, "marker": marker, "outcome": "executed"})
            self._text(200, f"executed {marker}\n")
            return

        if path == "/exec":
            marker = _next_marker("EXEC")
            try:
                # Falco 看的是這個 execve。來源 IP 來自 TCP 對端，放進 cmdline 後由
                # Grafana LogQL 擷取為 source_ip label；agent 不必也不得自行猜封鎖對象。
                subprocess.run(
                    ["/bin/sh", "-c", f"echo {marker} SOURCE_IP={source_ip}; id"],
                    capture_output=True, timeout=5, check=False,
                )
            except Exception as exc:  # noqa: BLE001
                self._text(500, f"exec error: {exc}")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/exec",
                        "source_ip": source_ip, "marker": marker, "outcome": "executed"})
            self._text(200, f"executed {marker}\n")
            return

        if path == "/readsecret":
            # SA §7 Scenario 03：敏感檔存取。open() 會被 Falco 抓到。
            try:
                with open(SECRET_PATH, "rb") as f:
                    size = len(f.read())
            except OSError as exc:
                _write_log({"ts": _now(), "app": "range-target", "path": "/readsecret",
                            "source_ip": source_ip, "outcome": "error", "error": str(exc)})
                self._text(500, f"read error: {exc}")
                return
            _write_log({"ts": _now(), "app": "range-target", "path": "/readsecret",
                        "source_ip": source_ip, "bytes": size, "outcome": "read"})
            self._text(200, f"read {size} bytes from {SECRET_PATH}\n")
            return

        if path == "/poster/render":
            self._poster_render(source_ip)
            return

        if path == "/preview":
            self._preview(source_ip)
            return

        if path == "/internal/reports":
            self._internal_reports(source_ip)
            return

        if path == "/diagnostics/lookup":
            self._diagnostics_lookup(source_ip)
            return

        if path == "/students/token":
            self._students_token(source_ip)
            return

        # `/students/<id>/records` —— 路徑帶變動的 id 片段，用切割比對而非整段
        # 字串相等（其餘端點都是固定路徑，這是唯一一個需要解析路徑參數的）。
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "students" and parts[2] == "records":
            self._student_records(source_ip, parts[1])
            return

        _write_log({"ts": _now(), "app": "range-target", "path": path,
                    "source_ip": source_ip, "outcome": "not_found"})
        self._text(404, "not found\n")

    def _students_token(self, source_ip: str) -> None:
        # 自助登入（FINAL 攻擊鏈第一步，T1087 帳號列舉的落地）：宣告 student_id
        # 就核發 token，不驗證身分。
        claimed_id = parse_qs(urlparse(self.path).query).get("student_id", [""])[0]
        if not claimed_id:
            self._text(400, "missing student_id\n")
            return
        token = _issue_student_token(claimed_id)
        _write_log({"ts": _now(), "app": "range-target", "path": "/students/token",
                    "source_ip": source_ip, "claimed_student_id": claimed_id,
                    "outcome": "issued"})
        self._text(201, token + "\n")

    def _student_records(self, source_ip: str, requested_id: str) -> None:
        # IDOR 讀取（FINAL 攻擊鏈第二/三步，T1213 讀取 + T1567 批次外洩的落地，
        # 兩者共用同一個端點——差別只在「用同一個 token 打幾個不同 id」，技術上
        # 不需要分開兩支端點）。
        token = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        if not _token_is_valid(token):
            _write_log({"ts": _now(), "app": "range-target", "path": "/students/records",
                        "source_ip": source_ip, "requested_id": requested_id,
                        "outcome": "denied"})
            self._text(401, "invalid or missing token\n")
            return

        # 認證通過（token 有效）；**沒有檢查這筆 id 是不是這個 token 的主人**——
        # 這就是漏洞本身。仍然把這個語意層事實寫進 log（遙測看得到），只是沒有
        # Grafana 規則會為它告警（見本檔開頭 FINAL 段落）。
        cross_id = _looks_like_idor_access(requested_id, token)
        _write_log({"ts": _now(), "app": "range-target", "path": "/students/records",
                    "source_ip": source_ip, "requested_id": requested_id,
                    "idor_cross_id_access": cross_id, "outcome": "read"})
        self._text(200, json.dumps(_fake_student_record(requested_id), ensure_ascii=False) + "\n")

    def _diagnostics_lookup(self, source_ip: str) -> None:
        # 校園報修診斷工具（CH4 計分攻擊面）：把 host 直接組進 shell 指令字串，
        # 沒有跳脫、沒有白名單。正常主機名只會跑到內建 echo；帶 shell 特殊字元
        # 就能斷句注入新指令——這是漏洞本身，不是某個弱檢查被繞過。
        raw_host = parse_qs(urlparse(self.path).query).get("host", [""])[0]
        if not raw_host:
            self._text(400, "missing host\n")
            return

        suspected = _looks_like_command_injection(raw_host)
        try:
            rc, output = _run_diagnostic(raw_host)
        except Exception as exc:  # noqa: BLE001 — 指令執行失敗原文回給紅隊，是這一步的一部分
            self._text(500, f"diagnostic error: {exc}")
            return

        _write_log({"ts": _now(), "app": "range-target", "path": "/diagnostics/lookup",
                    "source_ip": source_ip, "host": raw_host,
                    "cmd_injection_suspected": suspected, "outcome": "executed",
                    "returncode": rc})
        self._text(200, output)

    def _preview(self, source_ip: str) -> None:
        # 校園版「網址預覽」（CH3 計分攻擊面）：接受任意 url，伺服器端直接 fetch。
        # 沒有目的地白名單/黑名單 —— 這正是漏洞：攻擊者能借伺服器的手打伺服器
        # 自己碰得到、但外部連不到的位址（loopback 上的 metadata service）。
        target_url = parse_qs(urlparse(self.path).query).get("url", [""])[0]
        if not target_url:
            self._text(400, "missing url\n")
            return

        suspected = _looks_like_ssrf(target_url)
        try:
            status, body = _fetch_preview(target_url)
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            _write_log({"ts": _now(), "app": "range-target", "path": "/preview",
                        "source_ip": source_ip, "target_url": target_url,
                        "ssrf_suspected": suspected, "outcome": "fetch_error",
                        "error": str(exc)})
            self._text(502, f"fetch error: {exc}\n")
            return

        _write_log({"ts": _now(), "app": "range-target", "path": "/preview",
                    "source_ip": source_ip, "target_url": target_url,
                    "ssrf_suspected": suspected, "outcome": "fetched",
                    "upstream_status": status})
        self._text(200, body.decode(errors="replace"))

    def _internal_reports(self, source_ip: str) -> None:
        # 「原本要授權才能進」的內部 API（T1550 API pivot 的落地）。帶對憑證的
        # X-Internal-Token 才放行 —— 憑證只能透過 /preview 打 metadata service 偷到，
        # 平台本身不會把它交給一般使用者。CH3 v1 這一步不掛計分 objective
        # （見 app.py 開頭 CH3 段落與 docs/campaign/README.md 的平台限制說明）。
        token = self.headers.get("X-Internal-Token", "")
        if token != INTERNAL_API_TOKEN:
            _write_log({"ts": _now(), "app": "range-target", "path": "/internal/reports",
                        "source_ip": source_ip, "outcome": "denied"})
            self._text(403, "forbidden\n")
            return
        _write_log({"ts": _now(), "app": "range-target", "path": "/internal/reports",
                    "source_ip": source_ip, "outcome": "pivot_success"})
        self._text(200, "internal reports: campus enrollment figures q3...\n")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        source_ip = self.client_address[0]

        if path == "/poster/upload":
            self._poster_upload(source_ip)
            return

        _write_log({"ts": _now(), "app": "range-target", "path": path,
                    "source_ip": source_ip, "outcome": "not_found"})
        self._text(404, "not found\n")

    def _poster_upload(self, source_ip: str) -> None:
        # 校園海報／作業上傳（CH2 計分攻擊面）。`filename` 由呼叫端指定 —— 這是真實
        # 上傳表單常見的作法（表單另帶檔名欄位），不是本章要展示的漏洞本身。
        filename = parse_qs(urlparse(self.path).query).get("filename", [""])[0]
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""

        if not _is_safe_poster_name(filename):
            self._text(400, "invalid filename\n")
            return

        # 唯一的檢查：Content-Type 落在允許清單。這正是漏洞 —— 沒有驗證副檔名，
        # 也沒開檔看 magic bytes，攻擊者只要送對 header，.py 一樣被存下來。
        if content_type not in ALLOWED_POSTER_CONTENT_TYPES:
            _write_log({"ts": _now(), "app": "range-target", "path": "/poster/upload",
                        "source_ip": source_ip, "filename": filename,
                        "content_type": content_type, "outcome": "rejected_content_type"})
            self._text(415, "unsupported content type\n")
            return

        os.makedirs(POSTER_DIR, exist_ok=True)
        dest = os.path.join(POSTER_DIR, os.path.basename(filename))
        with open(dest, "wb") as f:
            f.write(body)

        bypassed = _poster_upload_bypasses_content_check(content_type, filename)
        _write_log({"ts": _now(), "app": "range-target", "path": "/poster/upload",
                    "source_ip": source_ip, "filename": filename,
                    "content_type": content_type, "outcome": "stored",
                    "upload_check_bypassed": bypassed})
        self._text(201, f"stored {filename}\n")

    def _poster_render(self, source_ip: str) -> None:
        name = parse_qs(urlparse(self.path).query).get("name", [""])[0]
        if not _is_safe_poster_name(name):
            self._text(400, "invalid name\n")
            return

        target = os.path.join(POSTER_DIR, os.path.basename(name))
        if not os.path.isfile(target):
            self._text(404, "poster not found\n")
            return

        if not name.lower().endswith(".py"):
            # 一般圖片：本 v1 不做真的縮圖處理，只回應「已渲染」——這不是計分攻擊面。
            _write_log({"ts": _now(), "app": "range-target", "path": "/poster/render",
                        "source_ip": source_ip, "name": name, "outcome": "rendered_static"})
            self._text(200, f"rendered {name}\n")
            return

        # 「進階自訂範本」：.py 副檔名的海報被當成 render pipeline 的腳本直接執行。
        # 這裡完全沒有再驗證內容 —— 上傳時只查過 Content-Type，執行時只看副檔名。
        # 兩層都只信任攻擊者能控制的欄位，這正是 Web Shell 這一步成立的原因。
        try:
            result = subprocess.run(
                ["python3", target],
                capture_output=True, timeout=5, check=False,
                preexec_fn=_drop_privileges_to_posterrender(),
            )
        except Exception as exc:  # noqa: BLE001 — 範本執行失敗原文回給紅隊，是這一步的一部分
            self._text(500, f"render error: {exc}")
            return

        _write_log({"ts": _now(), "app": "range-target", "path": "/poster/render",
                    "source_ip": source_ip, "name": name, "outcome": "executed_template"})
        output = result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
        self._text(200, output)

    def _text(self, code: int, msg: str) -> None:
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


def main() -> None:
    # 開機寫一行，讓 Loki 一定有這個 stream（查詢不會因無 stream 報錯）。
    _write_log({"ts": _now(), "app": "range-target", "event": "startup"})
    threading.Thread(target=_alloy_heartbeat_loop, daemon=True).start()
    # CH3：metadata service 只 bind 127.0.0.1，外部網路連不到——唯一入口是靶機
    # 自己被 SSRF 借用去打它。
    threading.Thread(target=_start_metadata_service, daemon=True).start()
    print(f"range-target app listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
