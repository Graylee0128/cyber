"""CH3 The Stolen Key（校園網址預覽 SSRF，#153 Campaign Pack v1）第三條真 scenario。

同 `test_first_real_scenario.py` 的性質分級：這裡守住手寫 YAML＋Markdown 的格式／
內容不漂移；真打 SSRF／真讀到 metadata／真 pivot 由
`tests/deploy/test_range_target_ssrf.py`（真 HTTP round-trip，不需要 VM）證。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from range_core.scenarios import ScenarioCatalog, load_scenario

REPO = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO / "scenarios" / "campus-preview-metadata-pivot"
METADATA = SCENARIO_DIR / "metadata.yaml"
BRIEFING = SCENARIO_DIR / "briefing.md"

# 攻擊路徑的關鍵字。briefing 出現任何一個就代表洩漏了「怎麼進去」（§4.2）。
ATTACK_PATH_LEAKS = [
    "ssrf", "preview", "metadata", "loopback", "127.0.0.1", "internal-token",
    "x-internal-token", "internal/reports", "credential", "t1190", "t1552", "t1550",
]


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(METADATA)


def test_metadata_passes_loader_validation(scenario):
    assert scenario.id == "campus-preview-metadata-pivot"
    assert scenario.id == SCENARIO_DIR.name


def test_it_is_in_the_production_catalog_alongside_ch1_and_ch2(scenario):
    catalog = ScenarioCatalog.from_directory(REPO / "scenarios")
    ids = {s.id for s in catalog.scenarios}
    assert {"shopdb-credential-pivot", "campus-poster-foothold",
            "campus-preview-metadata-pivot"} <= ids


def test_objectives_are_flat_with_no_prerequisites(scenario):
    for objective in scenario.objectives:
        assert not hasattr(objective, "requires")


def test_no_submission_objective_v1_platform_only_has_one_global_flag():
    """同 CH2：v1 只有一個全域 flag（`range_core.flags.SharedFileFlagSource`）。
    CH3 沿用同一個決定，不設 submission objective。"""
    scenario = load_scenario(METADATA)
    submission_objectives = [o for o in scenario.objectives if o.evaluation == "submission"]
    assert submission_objectives == []


def test_metadata_theft_is_telemetry_and_tied_to_the_second_attack_chain_action(scenario):
    by_id = {o.id: o for o in scenario.objectives}
    detect = by_id["detect-ssrf-metadata-theft"]
    assert detect.evaluation == "telemetry"
    assert detect.telemetry_signal is not None
    action_ids = {a.id for a in scenario.attack_chain}
    assert detect.telemetry_signal.action_id in action_ids
    assert detect.telemetry_signal.action_id == "ssrf-metadata-theft"


def test_api_pivot_is_an_intentional_detection_gap(scenario):
    """§2.2：讀到憑證有覆蓋（T1552），但憑證被拿去 pivot 的證據刻意較弱（T1550）——
    與 CH1 的 T1078 gap 同類型：遙測看得到（/internal/reports 有請求紀錄），
    沒有告警規則。"""
    assert "T1550" in scenario.intentional_gaps
    gap_action = next(a for a in scenario.attack_chain if a.technique == "T1550")
    assert gap_action.id == "internal-api-pivot"
    assert scenario.detection == ("EgressAnomalyTarget",)


def test_attack_chain_covers_ssrf_entry_theft_and_pivot(scenario):
    techniques = [a.technique for a in scenario.attack_chain]
    assert techniques == ["T1190", "T1552", "T1550"]


def test_single_host_depth_same_host_as_ch1_and_ch2(scenario):
    """§3.3：單機縱深，且與 CH1/CH2 同一台主機（校園世界觀：同一套系統的
    不同功能模組，見 docs/campaign/README.md）。"""
    assert len(scenario.targets) == 1
    assert scenario.targets[0].host == "range-target"


def test_reset_scope_is_exercise_not_environment(scenario):
    """§5.2：preview 只 fetch、metadata service 只回靜態值、internal/reports 只讀
    不寫——全程唯讀，與 CH2（寫檔，environment）刻意不同，exercise 級重置即可。"""
    assert scenario.reset_scope == "exercise"


def test_briefing_states_the_goal_without_leaking_the_attack_path():
    """§4.2：briefing 只給任務目標與規則，不透露攻擊路徑。"""
    text = BRIEFING.read_text(encoding="utf-8").lower()
    leaked = [kw for kw in ATTACK_PATH_LEAKS if kw in text]
    assert not leaked, f"briefing 洩漏了攻擊路徑關鍵字：{leaked}"
