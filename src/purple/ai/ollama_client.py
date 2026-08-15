"""#131：本機 AI 推論的薄殼客戶端 —— Ollama + qwen2.5:3b，不打外部 API。

跟 `range_core/response_dispatch.py`（#51）同一種取捨：刻意用 stdlib `urllib`，
不引入 httpx 之類的用戶端函式庫——這通呼叫同步、低頻（一份報告一次、一次
Instructor 摘要刷新一次），沒有需要非同步／連線池的規模。

**失敗一律回 `None`，不拋例外**——跟 `response_dispatch.py` 的 module docstring
講的是同一件事：AI 輔助是加分項，不是任何計分／證據路徑的必要條件（#132／#133
的驗收明講「AI 服務不可用時，既有功能照樣運作」）。呼叫端只要檢查 `None` 就知道
要不要顯示那段內容，不需要 try/except 包住呼叫。

**`timeout_s` 沒有一個對所有呼叫端都合理的預設值**——2026-08-15 在無 GPU 的
VM（6 vCPU）上實測，qwen2.5:3b 對一段中等長度的 prompt 跑超過 120 秒才回應。
這個預設值（見 `DEFAULT_TIMEOUT_S`）刻意抓寬，適合「演練結束後才生成一次」
的場景（#132 Exercise Report）；但即時互動的呼叫端（#133 Instructor Copilot，
教官畫面每次刷新都可能觸發一次）**必須自己傳一個短很多的 `timeout_s`**，
不能用預設值——不然一次逾時就會讓整個畫面刷新卡住兩分鐘，即使最終呼叫端會
拿到 `None` 優雅降級，使用者體感上也是「畫面卡住」，不是「AI 沒有摘要」。
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("purple.ai.ollama_client")

#: Z-MGMT 內部服務名稱，跟 docker-compose.yml 的 `ollama` service 對應。
BASE_URL_ENV = "OLLAMA_BASE_URL"
MODEL_ENV = "OLLAMA_MODEL"
DEFAULT_BASE_URL = "http://ollama:11434"
DEFAULT_MODEL = "qwen2.5:3b"
#: 抓寬給「離峰生成一次」的場景（見 module docstring）；即時互動場景要自己
#: 傳更短的 timeout_s，不要沿用這個預設值。
DEFAULT_TIMEOUT_S = 90.0


def generate(
    prompt: str,
    *,
    system: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> str | None:
    """跟本機 Ollama 要一段生成文字，失敗（含逾時、連不上、模型未拉）一律回 `None`。

    `stream=False`——一次拿完整回應，呼叫端（敘事生成、SOC Copilot）都是等一段
    短文字，用不到串流。
    """
    resolved_base_url = base_url or os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL)
    resolved_model = model or os.environ.get(MODEL_ENV, DEFAULT_MODEL)

    payload: dict[str, object] = {
        "model": resolved_model,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system

    request = urllib.request.Request(
        f"{resolved_base_url.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            if not (200 <= resp.status < 300):
                log.warning("ollama 回應非 2xx：%s", resp.status)
                return None
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("連不上 ollama（%s）——AI 輔助本次不可用", exc)
        return None
    except json.JSONDecodeError as exc:
        log.warning("ollama 回應不是合法 JSON（%s）", exc)
        return None

    text = body.get("response")
    if not isinstance(text, str) or not text.strip():
        log.warning("ollama 回應缺 response 欄位或是空字串")
        return None
    return text.strip()
