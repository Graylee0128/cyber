"""#44/#45/#46 契約 —— 靶機真攻擊面、flag 輪換、紅隊 image。

T1 只證**結構契約**（純函式可注入性、seed 授權邊界、golden_stamp 不含 flag、Dockerfile
工具清單）。真 SQLi 打進去、真拿到 flag、真 golden 重烤由大主機的 T2/T3/T4 證（見 PR runbook）。

app.py 與 flag_mint.py 不在套件路徑上，用 importlib 依檔案載入 —— pymysql 是 app.py 的
延後 import，載模組不會觸發它，所以本機無 DB 也跑得動這些純函式測試。
"""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


APP = _load(ROOT / "deploy" / "range-target" / "app.py")
FLAG_MINT = _load(ROOT / "scripts" / "range" / "flag_mint.py")

SEED = (ROOT / "deploy" / "range-target" / "seed.sql").read_text(encoding="utf-8")
APP_SRC = (ROOT / "deploy" / "range-target" / "app.py").read_text(encoding="utf-8")
STAMP_SRC = (ROOT / "scripts" / "range" / "lib-cloudimg.sh").read_text(encoding="utf-8")
BUILD_VM = (ROOT / "scripts" / "range" / "build-vm-target.sh").read_text(encoding="utf-8")
BUILD_GOLDEN = (ROOT / "scripts" / "range" / "build-golden-target.sh").read_text(encoding="utf-8")
BAKE = (ROOT / "deploy" / "range-target" / "bake.sh").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "deploy" / "red-attacker" / "Dockerfile").read_text(encoding="utf-8")
ATTACH_RED = (ROOT / "scripts" / "range" / "attach-red.sh").read_text(encoding="utf-8")


# ── #44 計分攻擊面：SQLi 必須真的可注入 ──────────────────────────────────
def test_product_query_is_genuinely_injectable():
    """UNION payload 原封不動出現在 SQL 裡 —— 沒有參數化、沒有 escaping。"""
    payload = "0 UNION SELECT id, service, username, password FROM credentials"
    sql = APP.build_product_query(payload)
    assert payload in sql
    # 若哪天有人把它改成參數化（占位符 %s / ?），這條會紅，提醒「計分攻擊面不能修」。
    assert "%s" not in sql and "?" not in sql


def test_benign_id_stays_scoped_to_one_row():
    assert APP.build_product_query("2") == (
        "SELECT id, name, price, description FROM products WHERE id = 2"
    )


# ── #44 二分：fixture 端點行為完全不變 ──────────────────────────────────
def test_fixture_endpoints_are_untouched():
    # 四個 fixture 路由與 alloy heartbeat 都還在，且新增的計分面沒有動到它們。
    for fixture in ('path == "/exec"', 'path == "/readsecret"',
                    'path == "/uncovered"', 'path == "/healthz"',
                    "emit_alloy_heartbeat"):
        assert fixture in APP_SRC
    # 計分面是**新增**的分支，不是改寫 fixture。
    assert 'path == "/product"' in APP_SRC


# ── #44 授權邊界：webapp 讀不到 flag，dbadmin 才讀得到（內容鏈接的實際 enforcement）──
def test_webapp_user_has_no_grant_on_the_flag_vault():
    # webapp 只被授 shopdb.*；對 vault 一條 GRANT 都沒有 —— SQLi 一跳撈不到 flag。
    webapp_grants = re.findall(r"GRANT[^;]*TO 'webapp'@'localhost'", SEED, re.IGNORECASE)
    assert webapp_grants, "webapp 應有 shopdb 授權"
    for grant in webapp_grants:
        assert "vault" not in grant.lower()


def test_dbadmin_is_the_only_door_to_the_flag_vault():
    # flag 住在 vault，且只有 dbadmin（憑證表裡那組帳密）grant 得到它。
    assert "vault.flag" in SEED
    vault_grants = re.findall(r"GRANT[^;]*vault[^;]*TO '([^']+)'", SEED, re.IGNORECASE)
    assert vault_grants, "應有人被授 vault 權限"
    assert all(user == "dbadmin" for user in vault_grants)


def test_flag_value_is_not_hardcoded_in_seed():
    # seed 只放 placeholder；真 flag 開機注入。seed 裡不得出現看似真 flag 的 32-hex。
    assert "PLACEHOLDER" in SEED
    assert not re.search(r"flag\{[0-9a-f]{32}\}", SEED)


# ── #45 flag 輪換 ───────────────────────────────────────────────────────
def test_flag_mint_is_unique_per_call():
    flags = {FLAG_MINT.mint_flag() for _ in range(50)}
    assert len(flags) == 50


def test_flag_mint_matches_the_declared_format():
    flag = FLAG_MINT.mint_flag()
    assert FLAG_MINT.is_valid_flag(flag)
    assert not FLAG_MINT.is_valid_flag("flag{too-short}")
    assert not FLAG_MINT.is_valid_flag("not-a-flag")


def test_flag_is_excluded_from_the_golden_fingerprint():
    """換 flag 不能觸發重烤 —— flag 相關來源不得進 golden_stamp 的檔案清單。"""
    stamp_fn = STAMP_SRC.split("golden_stamp()", 1)[1].split("\n}", 1)[0]
    for flag_source in ("flag.txt", "current-flag", "flag_mint"):
        assert flag_source not in stamp_fn
    # 反面：靜態的 seed 與 inject 腳本**必須**進指紋（改結構要重烤）。
    assert "seed.sql" in stamp_fn
    assert "inject-flag.sh" in stamp_fn


def test_flag_is_injected_at_boot_not_baked_into_golden():
    # range-up 產生 flag 並注入 VM 的 /etc/purplescope/flag.txt + 寫 Range Core 共享檔。
    assert "flag_mint.py" in BUILD_VM
    assert "/etc/purplescope/flag.txt" in BUILD_VM
    assert "FLAG_SHARE_FILE" in BUILD_VM
    # golden 烤圖腳本**不得**帶任何當場 flag 值 —— 它只烤 placeholder 的 seed 與注入腳本。
    assert not re.search(r"flag\{[0-9a-f]{32}\}", BUILD_GOLDEN)
    # 開機注入的 oneshot 存在。
    assert "purplescope-flag-inject.service" in BAKE


# ── #46 紅隊最小攻擊 image ───────────────────────────────────────────────
def test_red_image_carries_exactly_the_chain_tools():
    # 只看實際 install 的套件區塊（`apt-get install` → `rm -rf`），不掃註解 ——
    # 註解裡會解釋「為什麼不是 netshoot」，那不是安裝，不該被誤判。
    install_block = DOCKERFILE.split("apt-get install", 1)[1].split("rm -rf", 1)[0].lower()
    # 第一跳 curl、第二跳 mysql client —— 兩個都裝。
    assert "curl" in install_block
    assert "mariadb-client" in install_block
    # 「最小」：不預裝重量級掃描/爆破工具箱。
    for speculative in ("nmap", "metasploit", "sqlmap", "hydra"):
        assert speculative not in install_block


def test_attach_red_defaults_to_the_local_attack_image():
    assert "purplescope/red-attacker:latest" in ATTACH_RED
    assert 'RED_IMAGE:-purplescope/red-attacker:latest' in ATTACH_RED
    # 接無網 VLAN30 前先在 host build 好，接上後零安裝步驟。
    assert 'docker build -t "$RED_IMAGE"' in ATTACH_RED
