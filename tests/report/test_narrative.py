"""`purple.report.narrative`（#132）—— Exercise Report 的 AI 敘事生成。

`build_prompt` 是純函數，直接斷言輸出；`generate_narrative` 對外唯一出口，
用 monkeypatch 換掉 `purple.ai.ollama_client.generate` 驗證委派與失敗傳遞，
不需要真的起一台假 Ollama server（那個手法留給 `tests/ai/test_ollama_client.py`
驗 HTTP 細節，這裡只驗「有沒有正確呼叫、失敗有沒有正確傳遞」）。
"""

from __future__ import annotations

from purple.report import narrative as narrative_module
from purple.report.narrative import build_prompt, generate_narrative

REPORT_DICT = {
    "exercise_id": "ex-01",
    "red": {"attack_success_pct": 67, "objectives": "4/7"},
    "blue": {"action_coverage": 0.82, "alert_volume": 4120, "mttd_ms": 12400, "mttr_ms": 14200},
    "coverage_gaps": [
        {"technique": "T1059", "classification": "detection_gap"},
        {"technique": "T1071", "classification": "visibility_gap"},
    ],
    "unknown": {"count": 2, "reasons": ["Falco 於 14:52–14:58 掉線"]},
    "raw_coverage_windows": ["09:15 有 raw"],
    "recommendations": ["補 T1059 規則"],
    "narrative": None,
}


class TestBuildPrompt:
    def test_includes_red_and_blue_numbers_verbatim(self):
        prompt = build_prompt(REPORT_DICT)

        assert "67%" in prompt
        assert "4/7" in prompt
        assert "0.82" in prompt
        assert "4120" in prompt
        assert "12400" in prompt
        assert "14200" in prompt

    def test_includes_coverage_gaps_when_present(self):
        prompt = build_prompt(REPORT_DICT)

        assert "T1059" in prompt
        assert "detection_gap" in prompt
        assert "T1071" in prompt
        assert "visibility_gap" in prompt

    def test_omits_gap_line_when_no_gaps(self):
        report = {**REPORT_DICT, "coverage_gaps": []}
        prompt = build_prompt(report)

        assert "偵測缺口" not in prompt

    def test_includes_unknown_reasons_when_count_positive(self):
        prompt = build_prompt(REPORT_DICT)

        assert "2" in prompt
        assert "Falco 於 14:52–14:58 掉線" in prompt

    def test_omits_unknown_line_when_count_zero(self):
        report = {**REPORT_DICT, "unknown": {"count": 0, "reasons": []}}
        prompt = build_prompt(report)

        assert "無法判定" not in prompt


class TestGenerateNarrative:
    def test_delegates_to_ollama_client_with_system_prompt(self, monkeypatch):
        captured = {}

        def fake_generate(prompt, *, system=None, **kwargs):
            captured["prompt"] = prompt
            captured["system"] = system
            return "一段摘要"

        monkeypatch.setattr(narrative_module, "generate", fake_generate)

        result = generate_narrative(REPORT_DICT)

        assert result == "一段摘要"
        assert captured["prompt"] == build_prompt(REPORT_DICT)
        assert captured["system"] is not None
        assert "不得新增" in captured["system"] or "不得" in captured["system"]

    def test_none_from_ollama_client_passes_through(self, monkeypatch):
        """Ollama 不可用時 generate() 回 None——這裡原樣傳回，不拋例外、不補假資料。"""
        monkeypatch.setattr(narrative_module, "generate", lambda *a, **k: None)

        assert generate_narrative(REPORT_DICT) is None
