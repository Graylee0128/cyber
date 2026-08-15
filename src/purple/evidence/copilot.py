"""#133：Instructor SOC Copilot —— 摘要多個藍隊玩家目前的 alert／investigation 狀態。

**只給教官用，純呈現層**：輸入是呼叫端已經準備好的玩家狀態快照（`player_id`
＋選填的 `alert_count`／`current_action`），這裡不查資料庫、不查 Range Core、
不新增任何判斷——跟 `purple.report.narrative`（#132）同一種設計，只是把一份
已經算好的狀態表唸成一段話。**不寫回任何計分／證據欄位**（#133 驗收）。

`timeout_s` 刻意比 `ollama_client.DEFAULT_TIMEOUT_S` 短很多——教官畫面每次
刷新都可能觸發一次呼叫，2026-08-15 實測 CPU-only 推論可能耗時超過 120 秒，
沿用預設值會讓整個畫面刷新卡住那麼久。這裡短逾時换來的代價是：資源緊張的
機器上摘要經常拿不到——可接受，因為 Ollama 不可用時教官畫面本來就該照舊
可用（#133 驗收），寧可常常沒有摘要，也不要讓畫面卡住。
"""

from __future__ import annotations

from purple.ai.ollama_client import generate

#: 短逾時：即時互動場景，不能沿用 ollama_client 給「離峰生成一次」場景的預設值。
COPILOT_TIMEOUT_S = 15.0

_SYSTEM_PROMPT = (
    "你是資安演練教官的即時輔助助手。只根據使用者提供的玩家狀態摘要每位玩家"
    "目前在做什麼，不得新增、推測任何未提供的細節，不得對玩家表現下評價、"
    "不得建議下一步動作。字數控制在 200 字以內，不要用條列式。"
)


def build_prompt(player_statuses: list[dict[str, object]]) -> str:
    """純函數，不做 I/O。`player_statuses` 每筆至少要有 `player_id`。"""
    if not player_statuses:
        return "目前沒有任何藍隊玩家在線。"

    lines = ["藍隊玩家目前狀態："]
    for status in player_statuses:
        player_id = status.get("player_id", "unknown")
        parts = [f"玩家 {player_id}"]
        alert_count = status.get("alert_count")
        if alert_count is not None:
            parts.append(f"待處理告警 {alert_count} 筆")
        current_action = status.get("current_action")
        if current_action:
            parts.append(f"最近動作：{current_action}")
        lines.append("、".join(parts) + "。")
    return "\n".join(lines)


def generate_copilot_summary(player_statuses: list[dict[str, object]]) -> str | None:
    """對外唯一出口。Ollama 不可用／逾時時回 `None`，不拋例外。"""
    prompt = build_prompt(player_statuses)
    return generate(prompt, system=_SYSTEM_PROMPT, timeout_s=COPILOT_TIMEOUT_S)
