"""WS2 v1 的唯一真 scenario：shopdb-credential-pivot（票 #47）。

守住十個決策裡「手寫檔案沒有測試接得住」那條風險（spec §0.2／§6.3）：scenario 是手寫
YAML＋Markdown，這支測試就是接它的網 —— 格式/內容漂了就紅。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from range_core.scenarios import ScenarioCatalog, load_scenario

REPO = Path(__file__).resolve().parents[2]
SCENARIO_DIR = REPO / "scenarios" / "shopdb-credential-pivot"
METADATA = SCENARIO_DIR / "metadata.yaml"
BRIEFING = SCENARIO_DIR / "briefing.md"
SEED_SQL = REPO / "deploy" / "range-target" / "seed.sql"

# 攻擊路徑的關鍵字。briefing 出現任何一個就代表洩漏了「怎麼進去」（§4.2）。
ATTACK_PATH_LEAKS = [
    "sqli", "sql injection", "union", "select", "injection",
    "dbadmin", "credential", "3306", "mysql", "vault.flag", "'or", "or 1=1",
]


@pytest.fixture(scope="module")
def scenario():
    return load_scenario(METADATA)


def test_metadata_passes_loader_validation(scenario):
    """§42 的所有載入驗證都過（load_scenario 會跑 reference 檢查）。"""
    assert scenario.id == "shopdb-credential-pivot"
    # id 必須等於目錄名（loader 強制）。
    assert scenario.id == SCENARIO_DIR.name


def test_it_is_the_only_shipped_scenario(scenario):
    """WS2 v1 只交一個（§6.3）。production catalog 恰好一個真 scenario。"""
    catalog = ScenarioCatalog.from_directory(REPO / "scenarios")
    assert [s.id for s in catalog.scenarios] == ["shopdb-credential-pivot"]


def test_objectives_are_flat_with_no_prerequisites(scenario):
    """§4.1：objective 扁平。模型根本沒有 requires 欄位 —— 這條測試把「扁平」釘死，
    未來有人想加 requires 時，先過這關才行。"""
    for objective in scenario.objectives:
        assert not hasattr(objective, "requires")


def test_capture_flag_is_submission_and_technique_is_telemetry(scenario):
    """§詞：capture_flag 走提交；技術類走遙測自動判定，且 telemetry_signal 綁 action_id。"""
    by_id = {o.id: o for o in scenario.objectives}
    capture = by_id["capture-vault-flag"]
    assert capture.evaluation == "submission"
    assert capture.telemetry_signal is None

    detect = by_id["detect-catalog-sqli"]
    assert detect.evaluation == "telemetry"
    assert detect.telemetry_signal is not None
    # 遙測判定靠 action_id 關聯，指向 attack_chain 的真動作（不靠 technique＋時間窗）。
    action_ids = {a.id for a in scenario.attack_chain}
    assert detect.telemetry_signal.action_id in action_ids


def test_intentional_gap_is_a_detection_gap_not_a_covered_technique(scenario):
    """§2.2：刻意無覆蓋的 technique 明示在 intentional_gaps，且**不**在 detection 覆蓋內。

    T1078（撈到的憑證直連 DB）沒有任何 Grafana 規則 → 遙測看得到、沒有告警 →
    在 P2 側是偵測缺口而非可見性缺口。若哪天有人替 T1078 加了規則卻沒更新這裡，
    這條會逼他面對「這還算不算刻意缺口」。
    """
    assert "T1078" in scenario.intentional_gaps
    gap_action = next(a for a in scenario.attack_chain if a.technique == "T1078")
    assert gap_action.id == "dbadmin-vault-pivot"
    # detection 只引用有覆蓋的規則；缺口 technique 不該混進 detection。
    assert scenario.detection == ("SQLInjectionBurstTarget",)


def test_single_host_depth(scenario):
    """§3.3：單機縱深 —— targets 是清單，但只有一台。"""
    assert len(scenario.targets) == 1
    assert scenario.targets[0].host == "range-target"


def test_briefing_states_the_goal_without_leaking_the_attack_path():
    """§4.2：briefing 只給任務目標與規則，不透露攻擊路徑。"""
    text = BRIEFING.read_text(encoding="utf-8").lower()
    assert "flag" in text  # 有講要拿什麼
    leaked = [kw for kw in ATTACK_PATH_LEAKS if kw in text]
    assert not leaked, f"briefing 洩漏了攻擊路徑關鍵字：{leaked}"


def test_no_exploitation_means_no_flag():
    """§5.1／§3.2：未經利用取不到 flag —— 授權邊界是機制，不是說明。

    seed.sql 的 grant 是「內容鏈接」的實際 enforcement：web 帳號（webapp）只 grant
    得到 shopdb，對 vault **沒有任何 grant**。所以光靠 catalog 的 SQLi（以 webapp 執行）
    讀不到 vault.flag，非得先撈出 dbadmin 憑證、再以 dbadmin 重連不可。這條測試把那個
    邊界釘死：有人若哪天給 webapp 開了 vault 權限，flag 就不再需要利用即可拿到，這條會紅。
    """
    sql = SEED_SQL.read_text(encoding="utf-8")
    # 逐條抓 GRANT ... TO 'user'@'host'
    grants = re.findall(
        r"GRANT\s+[\w\s,]+?\s+ON\s+([\w.*]+)\s+TO\s+'([^']+)'@", sql, re.IGNORECASE
    )
    webapp_targets = {db for db, user in grants if user == "webapp"}
    dbadmin_targets = {db for db, user in grants if user == "dbadmin"}

    # web 帳號拿得到 shopdb，但**碰不到 vault**。
    assert any(t.startswith("shopdb") for t in webapp_targets)
    assert not any(t.startswith("vault") for t in webapp_targets), (
        f"webapp 竟然對 vault 有 grant（{webapp_targets}）—— 不利用漏洞就能拿 flag，攻擊鏈失去意義"
    )
    # 唯一能讀 vault 的是 dbadmin（第二道門的鑰匙）。
    assert any(t.startswith("vault") for t in dbadmin_targets)
