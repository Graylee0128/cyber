"""CH2 Foothold（校園海報上傳，#153 Campaign Pack v1）提權腳的結構契約。

同 `test_target_attack_surface.py` 的性質分級：T1 只證**結構**——posterrender 帳號、
sudoers 誤設、bake 期自證、host 端 gate 都「有寫且寫對」；真的在 golden VM 上
useradd／visudo／sudo 是否真的成立，只能在大主機驗（T4，見 RUNBOOK CH2 段）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BAKE = (ROOT / "deploy" / "range-target" / "bake.sh").read_text(encoding="utf-8")
BUILD_GOLDEN = (ROOT / "scripts" / "range" / "build-golden-target.sh").read_text(encoding="utf-8")


# ── posterrender 帳號與目錄 ─────────────────────────────────────────────
def test_posterrender_is_a_system_account_without_login():
    assert "useradd --system --no-create-home --shell /usr/sbin/nologin posterrender" in BAKE


def test_poster_dir_is_owned_by_posterrender():
    assert "chown posterrender:posterrender /var/lib/purplescope/posters" in BAKE


# ── sudoers 誤設：這是漏洞本身，測試要釘住它「仍然不安全」 ──────────────────
def test_sudoers_rule_grants_posterrender_unrestricted_find_args():
    """漏洞本身：find 的引數沒有鎖死，尾端是裸 `*`。若哪天有人「好心」把它改成
    鎖死引數（例如加上 `-delete` 之類的固定尾綴），CH2 的提權步驟就會失效——
    這條測試把「仍然可利用」釘住，而不是釘住「規則存在」。"""
    assert "posterrender ALL=(root) NOPASSWD: /usr/bin/find /var/lib/purplescope/posters *" in BAKE


def test_sudoers_file_is_syntax_checked_before_bake_continues():
    # sudoers 語法錯誤會讓整台機器的 sudo 全部失效——故意留漏洞，但不能留語法自爆。
    assert "visudo -c -f /etc/sudoers.d/purplescope-poster" in BAKE
    assert "chmod 0440 /etc/sudoers.d/purplescope-poster" in BAKE


# ── bake 期自證：真的走一次上傳→執行→提權 ─────────────────────────────────
def test_bake_self_cert_actually_uploads_and_renders_a_python_payload():
    assert '"http://127.0.0.1/poster/upload?filename=selfcert.py"' in BAKE
    assert '"http://127.0.0.1/poster/render?name=selfcert.py"' in BAKE


def test_bake_self_cert_actually_attempts_the_sudo_escalation():
    assert "sudo -u posterrender sudo -n find /var/lib/purplescope/posters" in BAKE
    assert "-exec whoami" in BAKE


def test_bake_prints_ch2_state_marker_with_webshell_and_privesc_evidence():
    assert "GOLDEN-CH2-STATE:" in BAKE
    assert "webshell_hits=" in BAKE
    assert "privesc_whoami=" in BAKE


# ── host 端 gate：webshell 沒命中或提權沒到 root 就不產 golden ──────────────
def test_host_gate_requires_webshell_falco_hit_and_root_privesc():
    assert "GOLDEN-CH2-STATE" in BUILD_GOLDEN
    assert "privesc_whoami=root" in BUILD_GOLDEN
    assert 'bake_fail=1' in BUILD_GOLDEN
