"""FINAL The Leak（校園學生資料自助查詢 IDOR，#153 Campaign Pack v1）第五條、也是
最後一條 v1 真 scenario。同 `test_first_real_scenario.py` 的性質分級：這裡守住手寫
YAML＋Markdown 的格式／內容不漂移；真 IDOR／真批次讀取由
`tests/deploy/test_range_target_idor.py`（真 HTTP round-trip，不需要 VM）證。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from range_core.scenarios import ScenarioCatalog, load_scenario

REPO = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO / "scenarios" / "campus-student-records-idor"
METADATA = SCENARIO_DIR / "metadata.yaml"
BRIEFING = SCENARIO_DIR / "briefing.md"

# 攻擊路徑的關鍵字。briefing 出現任何一個就代表洩漏了「怎麼進去」（§4.2）。
ATTACK_PATH_LEAKS = [
    "idor", "token", "student_id", "records?", "object", "authoriz",
    "t1087", "t1213", "t1567",
]


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(METADATA)


def test_metadata_passes_loader_validation(scenario):
    assert scenario.id == "campus-student-records-idor"
    assert scenario.id == SCENARIO_DIR.name


def test_it_is_in_the_production_catalog_with_all_five_chapters(scenario):
    catalog = ScenarioCatalog.from_directory(REPO / "scenarios")
    ids = {s.id for s in catalog.scenarios}
    assert {
        "shopdb-credential-pivot",
        "campus-poster-foothold",
        "campus-preview-metadata-pivot",
        "campus-diagnostics-persistence",
        "campus-student-records-idor",
    } <= ids


def test_objectives_are_flat_with_no_prerequisites(scenario):
    for objective in scenario.objectives:
        assert not hasattr(objective, "requires")


def test_no_submission_objective_v1_platform_only_has_one_global_flag():
    """同 CH2/CH3/CH4：v1 只有一個全域 flag。FINAL 沿用同一個決定。"""
    scenario = load_scenario(METADATA)
    submission_objectives = [o for o in scenario.objectives if o.evaluation == "submission"]
    assert submission_objectives == []


def test_account_discovery_is_telemetry_and_tied_to_the_first_attack_chain_action(scenario):
    """這是 FINAL 唯一有偵測覆蓋的一步——刻意留給教學上較不重要的早期偵察
    （帳號列舉），不是核心 IDOR 讀取（見下方測試）。"""
    by_id = {o.id: o for o in scenario.objectives}
    detect = by_id["detect-account-discovery"]
    assert detect.evaluation == "telemetry"
    assert detect.telemetry_signal is not None
    action_ids = {a.id for a in scenario.attack_chain}
    assert detect.telemetry_signal.action_id in action_ids
    assert detect.telemetry_signal.action_id == "token-self-issue-enumeration"


def test_the_actually_interesting_steps_are_intentional_gaps(scenario):
    """§2.2：這是 FINAL 存在的理由——真正有教學意義的兩步（IDOR 讀取、批次外洩）
    刻意零覆蓋，只留一個教學上較次要的早期步驟（帳號列舉）有覆蓋。與 CH1 的單點
    gap、CH3 的部分覆蓋、CH2/CH4 的全覆蓋相比，FINAL 是四條新章節裡 gap 最重的
    一條——覆蓋程度刻意分層：CH2 全覆蓋、CH3 部分覆蓋、CH4 全覆蓋、FINAL 兩個 gap。
    """
    assert set(scenario.intentional_gaps) == {"T1213", "T1567"}
    assert scenario.detection == ("AccountDiscoveryTarget",)

    gap_actions = {a.technique: a.id for a in scenario.attack_chain if a.technique in scenario.intentional_gaps}
    assert gap_actions["T1213"] == "idor-record-read"
    assert gap_actions["T1567"] == "bulk-record-exfiltration"


def test_attack_chain_covers_discovery_read_and_exfiltration(scenario):
    techniques = [a.technique for a in scenario.attack_chain]
    assert techniques == ["T1087", "T1213", "T1567"]


def test_single_host_depth_same_host_as_earlier_chapters(scenario):
    """§3.3：單機縱深，且與 CH1–CH4 同一台主機（校園世界觀：同一套系統的
    不同功能模組）。"""
    assert len(scenario.targets) == 1
    assert scenario.targets[0].host == "range-target"


def test_reset_scope_is_exercise_tokens_are_in_memory_only(scenario):
    """§5.2：token 只存在記憶體（程序重啟即清空），讀取端點不寫檔——全程對
    檔案系統唯讀，與 CH2/CH4（寫檔，environment）刻意不同。"""
    assert scenario.reset_scope == "exercise"


def test_briefing_states_the_goal_without_leaking_the_attack_path():
    """§4.2：briefing 只給任務目標與規則，不透露攻擊路徑。"""
    text = BRIEFING.read_text(encoding="utf-8").lower()
    leaked = [kw for kw in ATTACK_PATH_LEAKS if kw in text]
    assert not leaked, f"briefing 洩漏了攻擊路徑關鍵字：{leaked}"
