"""`purple.evidence.copilot`（#133）—— Instructor SOC Copilot 的敘事生成。

同 `tests/report/test_narrative.py` 的手法：`build_prompt` 純函數直接斷言，
`generate_copilot_summary` 用 monkeypatch 換掉 `ollama_client.generate` 驗
委派與失敗傳遞。
"""

from __future__ import annotations

from purple.evidence import copilot as copilot_module
from purple.evidence.copilot import build_prompt, generate_copilot_summary


class TestBuildPrompt:
    def test_no_players_online(self):
        assert build_prompt([]) == "目前沒有任何藍隊玩家在線。"

    def test_includes_player_id(self):
        prompt = build_prompt([{"player_id": "blue-01"}])
        assert "blue-01" in prompt

    def test_includes_alert_count_when_present(self):
        prompt = build_prompt([{"player_id": "blue-01", "alert_count": 3}])
        assert "3" in prompt

    def test_omits_alert_count_when_absent(self):
        prompt = build_prompt([{"player_id": "blue-01"}])
        assert "待處理告警" not in prompt

    def test_includes_current_action_when_present(self):
        prompt = build_prompt([{"player_id": "blue-01", "current_action": "contain"}])
        assert "contain" in prompt

    def test_multiple_players_each_get_a_line(self):
        prompt = build_prompt(
            [{"player_id": "blue-01"}, {"player_id": "blue-02"}]
        )
        assert "blue-01" in prompt
        assert "blue-02" in prompt

    def test_unknown_player_id_falls_back_gracefully(self):
        prompt = build_prompt([{}])
        assert "unknown" in prompt


class TestGenerateCopilotSummary:
    def test_delegates_to_ollama_client_with_short_timeout(self, monkeypatch):
        captured = {}

        def fake_generate(prompt, *, system=None, timeout_s=None, **kwargs):
            captured["prompt"] = prompt
            captured["system"] = system
            captured["timeout_s"] = timeout_s
            return "摘要文字"

        monkeypatch.setattr(copilot_module, "generate", fake_generate)

        statuses = [{"player_id": "blue-01", "alert_count": 2}]
        result = generate_copilot_summary(statuses)

        assert result == "摘要文字"
        assert captured["prompt"] == build_prompt(statuses)
        # 即時互動場景，逾時必須明顯短於 ollama_client 的預設值（見該模組
        # docstring 對這個實測結果的說明）。
        assert captured["timeout_s"] == copilot_module.COPILOT_TIMEOUT_S
        assert captured["timeout_s"] < 30

    def test_none_from_ollama_client_passes_through(self, monkeypatch):
        monkeypatch.setattr(copilot_module, "generate", lambda *a, **k: None)

        assert generate_copilot_summary([{"player_id": "blue-01"}]) is None

    def test_no_players_does_not_call_ollama_at_all(self, monkeypatch):
        """2026-08-15 實測：沒東西可摘要時還是打了 Ollama，白白吃最多 15 秒逾時——
        教官畫面最常見的狀態（還沒開場）反而最容易卡住。改成直接回固定文字，
        不打網路。這條測試證明 generate() 完全沒被呼叫。"""
        called = []
        monkeypatch.setattr(copilot_module, "generate", lambda *a, **k: called.append(1) or "x")

        result = generate_copilot_summary([])

        assert result == "目前沒有任何藍隊玩家在線。"
        assert called == []
