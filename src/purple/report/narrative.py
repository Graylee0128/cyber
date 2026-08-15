"""#132：Exercise Report 的 AI 敘事生成 —— 純呈現層，不新增判斷權。

輸入是 `ExerciseReport.as_dict()` 已經凍結的數字；輸出只是把這些數字唸成
一段可讀的中文摘要。C1/C2/C3 證據分級、coverage gap 分類，都是既有 P2 判讀
的結果——AI 不重新判斷、不能覆寫任何欄位（見 purple_platform_plan.md §7 Q7：
「紫隊有 log 分析權」是既有定位，AI 動判讀等於推翻它）。

Ollama 不可用時（`generate()` 回 `None`），這裡也回 `None`——報告照樣產出，
只是沒有敘事段落（#132 驗收）。
"""

from __future__ import annotations

from purple.ai.ollama_client import generate

#: 刻意在 system prompt 就把「不要新增數字」寫死——防止模型把「摘要」做成
#: 「腦補」，尤其小模型（3b）更容易在字數不夠時自己編細節填空。
_SYSTEM_PROMPT = (
    "你是資安演練報告的摘要助手。只根據使用者提供的數字寫一段繁體中文敘事摘要，"
    "不得新增、推測或修改任何數字，不得提及輸入中沒有出現的技法、事件或建議。"
    "字數控制在 150 字以內，不要用條列式，寫成連貫的段落。"
)


def build_prompt(report_dict: dict[str, object]) -> str:
    """從 `ExerciseReport.as_dict()` 組出給 AI 的 prompt —— 純函數，不做 I/O。

    只搬既有欄位、不做任何換算——換算是 Evaluation API／`build_exercise_report`
    的責任，這裡再算一次等於開了第二份真相來源。
    """
    red = report_dict["red"]
    blue = report_dict["blue"]
    gaps = report_dict["coverage_gaps"]
    unknown = report_dict["unknown"]

    lines = [
        f"紅隊：攻擊成功率 {red['attack_success_pct']}%，目標完成 {red['objectives']}。",
        (
            f"藍隊：action coverage {blue['action_coverage']}，"
            f"告警總量 {blue['alert_volume']}，"
            f"MTTD {blue['mttd_ms']}ms，MTTR {blue['mttr_ms']}ms。"
        ),
    ]
    if gaps:
        gap_desc = "、".join(f"{g['technique']}（{g['classification']}）" for g in gaps)
        lines.append(f"偵測缺口：{gap_desc}。")
    if unknown["count"] > 0:
        reasons = "；".join(unknown["reasons"])
        lines.append(f"有 {unknown['count']} 項無法判定，原因：{reasons}。")

    return "\n".join(lines)


def generate_narrative(report_dict: dict[str, object]) -> str | None:
    """對外唯一出口。Ollama 不可用時回 `None`，不拋例外（呼叫端不用包 try/except）。"""
    prompt = build_prompt(report_dict)
    return generate(prompt, system=_SYSTEM_PROMPT)
