"""#62 WS8 spec §6.1／§6.2 —— Z-BLUE 座位 image 的結構契約。

T1 只證**結構契約**：非 root 帳號、sudo allowlist 不含 Falco/Alloy 路徑。
真的在容器裡 `sudo -l` 驗證、真的建立 ttyd 連線，屬 T3/T4，見 #62 PR 的
真環境驗證證據（不在本檔——本檔不碰 docker）。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "deploy" / "blue-seat" / "Dockerfile").read_text(encoding="utf-8")
SUDOERS = (ROOT / "deploy" / "blue-seat" / "sudoers.d" / "blue").read_text(encoding="utf-8")

# WS8 spec §6.2 硬約束：sudo allowlist 不得包含這些字樣。刻意用寬鬆的字串比對
# （不是精確路徑），寧可誤殺一條合法規則也不要漏放一個能碰到 collector 的洞——
# 這條測試存在的唯一理由就是把「絕對不行」寫成機器能檢查的東西。
FORBIDDEN_SUDOERS_TOKENS = (
    "falco",
    "alloy",
    "systemctl",
    "service ",
    "/etc/systemd",
    "ALL=(ALL) NOPASSWD: ALL",
    "ALL=(root) ALL",
)


def test_sudoers_allowlist_has_no_collector_tampering_path():
    # 只看有效規則行（非註解、非空白）——說明文件裡提到 falco/alloy 是在解釋
    # 「為什麼」，不是在放行規則，不該被這條檢查誤判。
    active = "\n".join(
        line for line in SUDOERS.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ).lower()
    for token in FORBIDDEN_SUDOERS_TOKENS:
        assert token.lower() not in active, f"sudoers 有效規則出現禁止字樣：{token}"


def test_sudoers_grants_nothing_wide_open():
    """不是「沒寫 falco」就過關——也不能整條放行成萬用 root。"""
    active_lines = [
        line for line in SUDOERS.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert active_lines == [], (
        "v1 sudoers allowlist 應該只有註解、不放行任何指令；"
        "要加規則見同檔開頭的說明（一行一條、指名完整路徑）"
    )


def test_container_runs_as_non_root_blue_user():
    assert "useradd" in DOCKERFILE and "-m -s /bin/bash blue" in DOCKERFILE
    assert "USER blue" in DOCKERFILE
    # USER blue 必須在 CMD 之前生效，不能是裝完東西才切换但 CMD 又切回去。
    assert DOCKERFILE.index("USER blue") < DOCKERFILE.index("CMD")


def test_sudoers_file_is_installed_with_restrictive_permissions():
    assert "chmod 0440 /etc/sudoers.d/blue" in DOCKERFILE
    assert "visudo -c" in DOCKERFILE  # build 期就驗證語法，壞掉的 sudoers 直接擋 build


def test_image_installs_real_ttyd_not_the_t3_stub():
    assert "apt-get install" in DOCKERFILE and "ttyd" in DOCKERFILE
    assert "ttyd" in DOCKERFILE.rsplit("CMD", 1)[1]
