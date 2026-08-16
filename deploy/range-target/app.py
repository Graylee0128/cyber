"""靶機 VM 的受攻擊面（Slice 4 / 票 #9；CH2 Foothold 見 #153）—— 跑在 Z-TARGET(VLAN20) 的真 VM 內。

與 compose 的 vulnerable-app 不同：這支是給**紅隊容器隔著真 VLAN 打**的，而偵測靠
同一台 VM 內的 Falco（modern-eBPF）看 syscall，不是靠 app 自己判斷。

端點：
- `/exec`         生一個帶 PURPLESCOPE_EXEC 標記的 shell → Falco 抓 execve → T1059
- `/readsecret`   讀 /etc/purplescope/secret.txt        → Falco 抓 open   → T1005（SA §7 Scenario 03）
- `/healthz`      存活探測
- `/poster/upload` `/poster/render`  海報上傳→自訂範本執行（CH2 計分攻擊面，見下方段落）

每個請求寫一行 JSON 到 app log（含 source_ip）。Alloy 把 app log 與 Falco events.json
一起推到 Z-MGMT 的 Loki —— 那條 TARGET→MGMT :3100 就是契約 1 的實用。
app log 裡的 source_ip 也是「六台紅隊 source IP 可分辨」在真環境的觀測點。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
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

        _write_log({"ts": _now(), "app": "range-target", "path": path,
                    "source_ip": source_ip, "outcome": "not_found"})
        self._text(404, "not found\n")

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
    print(f"range-target app listening on :{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
