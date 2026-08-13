"""服務身分 —— 呼叫者身分由部署時注入的 token 換出，不接受呼叫端自填。

WS7 spec §2。兩個對外出口（`purple.evidence` 的 Evidence API、`range_core` 的
Range Core API）**共用這一份**換出邏輯：識別身分只有一條路，漏做一邊不會有錯誤
訊號，只會讓呼叫端自報身分悄悄復活 —— 與 #52 B1 把遮蔽規則收成單一來源同理。

token 走環境變數注入（`<PREFIX><IDENTITY 大寫>`），與 `deploy/` 既有設定注入方式
一致，不引入新的機密管理機制（WS7 spec §2.4）。每個出口用**自己的 prefix**，
一個服務的 token 外洩不會連帶換出另一個服務的身分。
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from disclosure.clearance import CALLER_CLEARANCE

#: 唯一被承認的身分來源。任何「呼叫端直接填身分」的欄位都不看。
TOKEN_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "


def load_service_tokens(prefix: str, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """由部署環境變數建出 token → identity 對照表（純函數，env 可注入好測）。

    命名慣例：`<prefix><IDENTITY 大寫>`。哪個身分沒設對應變數，那個身分就沒有
    合法 token —— fail closed，不是「沒設定就預設放行」。
    """
    source = env if env is not None else os.environ
    tokens: dict[str, str] = {}
    for identity in CALLER_CLEARANCE:
        value = source.get(f"{prefix}{identity.upper()}")
        if value:
            tokens[value] = identity
    return tokens


def extract_token(headers: Mapping[str, str]) -> str | None:
    """由 `Authorization: Bearer <token>` 取出 token；其餘欄位一律忽略 ——

    身分只能經 token 換出，沒有任何「呼叫端直接填身分」的欄位（舊的
    `X-Purple-Identity` 就算送了也沒有作用）。
    """
    value = headers.get(TOKEN_HEADER, "")
    if value.startswith(BEARER_PREFIX):
        return value[len(BEARER_PREFIX):] or None
    return None


def resolve_identity(token: str | None, token_map: Mapping[str, str]) -> str | None:
    """token 換出 identity。查無此 token → None，絕不回退成任何預設身分。"""
    if not token:
        return None
    return token_map.get(token)
