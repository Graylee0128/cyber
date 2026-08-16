"""CH4 Ghost in the System（校園報修診斷工具，#153 Campaign Pack v1）第四條真 scenario。

同 `test_first_real_scenario.py` 的性質分級：這裡守住手寫 YAML＋Markdown 的格式／
內容不漂移；真指令注入／真寫進 cron.d 由
`tests/deploy/test_range_target_command_injection.py`（真 HTTP round-trip，不需要
VM）證。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from range_core.scenarios import ScenarioCatalog, load_scenario

REPO = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO / "scenarios" / "campus-diagnostics-persistence"
METADATA = SCENARIO_DIR / "metadata.yaml"
BRIEFING = SCENARIO_DIR / "briefing.md"

# 攻擊路徑的關鍵字。briefing 出現任何一個就代表洩漏了「怎麼進去」（§4.2）。
ATTACK_PATH_LEAKS = [
    "shell", "inject", "cron", "diagnostics/lookup", ";", "|", "`", "$(",
    "t1190", "t1059", "t1053",
]


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(METADATA)


def test_metadata_passes_loader_validation(scenario):
    assert scenario.id == "campus-diagnostics-persistence"
    assert scenario.id == SCENARIO_DIR.name


def test_it_is_in_the_production_catalog_alongside_earlier_chapters(scenario):
    catalog = ScenarioCatalog.from_directory(REPO / "scenarios")
    ids = {s.id for s in catalog.scenarios}
    assert {
        "shopdb-credential-pivot",
        "campus-poster-foothold",
        "campus-preview-metadata-pivot",
        "campus-diagnostics-persistence",
    } <= ids


def test_objectives_are_flat_with_no_prerequisites(scenario):
    for objective in scenario.objectives:
        assert not hasattr(objective, "requires")


def test_no_submission_objective_v1_platform_only_has_one_global_flag():
    """同 CH2/CH3：v1 只有一個全域 flag。CH4 沿用同一個決定。"""
    scenario = load_scenario(METADATA)
    submission_objectives = [o for o in scenario.objectives if o.evaluation == "submission"]
    assert submission_objectives == []


def test_cron_persistence_is_telemetry_and_tied_to_the_third_attack_chain_action(scenario):
    by_id = {o.id: o for o in scenario.objectives}
    detect = by_id["detect-cron-persistence"]
    assert detect.evaluation == "telemetry"
    assert detect.telemetry_signal is not None
    action_ids = {a.id for a in scenario.attack_chain}
    assert detect.telemetry_signal.action_id in action_ids
    assert detect.telemetry_signal.action_id == "cron-persistence-write"


def test_full_chain_has_no_intentional_gap_like_ch2(scenario):
    """§2.2：CH4 刻意「全覆蓋」（同 CH2），與 CH1/CH3 的單點/部分 gap、FINAL 的
    整條 gap 形成對照——四條新章節在覆蓋程度上刻意有層次（全覆蓋/部分/零）。"""
    assert scenario.intentional_gaps == ()
    assert set(scenario.detection) == {"CommandInjectionTarget", "CronPersistenceTarget"}


def test_attack_chain_covers_injection_shell_and_persistence(scenario):
    techniques = [a.technique for a in scenario.attack_chain]
    assert techniques == ["T1190", "T1059", "T1053"]


def test_single_host_depth_same_host_as_earlier_chapters(scenario):
    """§3.3：單機縱深，且與 CH1/CH2/CH3 同一台主機。"""
    assert len(scenario.targets) == 1
    assert scenario.targets[0].host == "range-target"


def test_reset_scope_is_environment_the_strongest_case_so_far(scenario):
    """§5.2：cron 檔跨 session 存活，是四條裡唯一會留下「重開機後依然生效」狀態
    的一條——environment 級重置的必要性最強。"""
    assert scenario.reset_scope == "environment"


def test_briefing_states_the_goal_without_leaking_the_attack_path():
    """§4.2：briefing 只給任務目標與規則，不透露攻擊路徑。"""
    text = BRIEFING.read_text(encoding="utf-8").lower()
    leaked = [kw for kw in ATTACK_PATH_LEAKS if kw in text]
    assert not leaked, f"briefing 洩漏了攻擊路徑關鍵字：{leaked}"
