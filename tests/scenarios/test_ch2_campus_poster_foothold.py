"""CH2 Foothold（校園海報上傳，#153 Campaign Pack v1）第二條真 scenario。

同 `test_first_real_scenario.py` 的性質分級：這裡守住手寫 YAML＋Markdown 的格式／
內容不漂移；真打進去／真拿到 root／真重烤由大主機的 T4 證（見 RUNBOOK CH2 段）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from range_core.scenarios import ScenarioCatalog, load_scenario

REPO = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO / "scenarios" / "campus-poster-foothold"
METADATA = SCENARIO_DIR / "metadata.yaml"
BRIEFING = SCENARIO_DIR / "briefing.md"

# 攻擊路徑的關鍵字。briefing 出現任何一個就代表洩漏了「怎麼進去」（§4.2）。
ATTACK_PATH_LEAKS = [
    "upload", "content-type", "content type", "png", "jpeg", ".py", "python",
    "render", "template", "sudo", "find", "gtfobins", "posterrender",
    "t1190", "t1505", "t1548",
]


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(METADATA)


def test_metadata_passes_loader_validation(scenario):
    """§42 的所有載入驗證都過（load_scenario 會跑 reference 檢查：expected_sources
    在 scenario-sources.yaml、detection 在 grafana rules.yaml、technique 在
    techniques.yaml 都存在）。"""
    assert scenario.id == "campus-poster-foothold"
    assert scenario.id == SCENARIO_DIR.name


def test_it_is_in_the_production_catalog_alongside_ch1(scenario):
    catalog = ScenarioCatalog.from_directory(REPO / "scenarios")
    ids = {s.id for s in catalog.scenarios}
    assert "campus-poster-foothold" in ids
    assert "shopdb-credential-pivot" in ids


def test_objectives_are_flat_with_no_prerequisites(scenario):
    """§4.1：objective 扁平。"""
    for objective in scenario.objectives:
        assert not hasattr(objective, "requires")


def test_no_submission_objective_v1_platform_only_has_one_global_flag():
    """平台限制（#153 CH2 實作時發現）：v1 只有一個全域 flag
    （`range_core.flags.SharedFileFlagSource`，環境級輪換、非逐 scenario）。Campaign
    會讓多個 scenario 同時開，若 CH2 也設 submission objective，玩家撈到 CH1 的 flag
    就能直接拿去交 CH2、完全不用碰 CH2 的漏洞 —— 這是真的評分漏洞。這條測試釘住
    「CH2 v1 不設 submission」這個刻意的範圍縮減；#45（逐 scenario 獨立 flag）落地後
    才適合放寬。"""
    scenario = load_scenario(METADATA)
    submission_objectives = [o for o in scenario.objectives if o.evaluation == "submission"]
    assert submission_objectives == []


def test_render_exec_is_telemetry_and_tied_to_the_second_attack_chain_action(scenario):
    by_id = {o.id: o for o in scenario.objectives}
    detect = by_id["detect-webshell-render"]
    assert detect.evaluation == "telemetry"
    assert detect.telemetry_signal is not None
    action_ids = {a.id for a in scenario.attack_chain}
    assert detect.telemetry_signal.action_id in action_ids
    assert detect.telemetry_signal.action_id == "webshell-render-exec"


def test_full_chain_has_no_intentional_gap_unlike_ch1_and_final(scenario):
    """§2.2：CH2 刻意「全覆蓋」，與 CH1 的 T1078 gap、FINAL 的整條 gap 形成對照 ——
    CH2 要證明的是「攻擊被完整看見」，不是「看不見」。"""
    assert scenario.intentional_gaps == ()
    assert set(scenario.detection) == {"WebShellUploadTarget", "LocalPrivescTarget"}


def test_attack_chain_covers_upload_webshell_and_privesc(scenario):
    techniques = [a.technique for a in scenario.attack_chain]
    assert techniques == ["T1190", "T1505", "T1548"]


def test_single_host_depth(scenario):
    """§3.3：單機縱深 —— targets 是清單，但只有一台，且與 CH1 同一台主機
    （校園世界觀：同一套系統的不同功能模組，見 docs/campaign/README.md）。"""
    assert len(scenario.targets) == 1
    assert scenario.targets[0].host == "range-target"


def test_reset_scope_is_environment_not_exercise(scenario):
    """§5.2：上傳的檔案會弄髒 POSTER_DIR，非唯讀 —— 與 CH1（唯讀 SELECT，
    reset_scope=exercise）刻意不同，需要環境級重置（重跑 range-up）。"""
    assert scenario.reset_scope == "environment"


def test_briefing_states_the_goal_without_leaking_the_attack_path():
    """§4.2：briefing 只給任務目標與規則，不透露攻擊路徑。"""
    text = BRIEFING.read_text(encoding="utf-8").lower()
    leaked = [kw for kw in ATTACK_PATH_LEAKS if kw in text]
    assert not leaked, f"briefing 洩漏了攻擊路徑關鍵字：{leaked}"
