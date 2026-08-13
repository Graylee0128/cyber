"""#26 權限邊界（T1 結構代理）。

AC 要求「從 Console 所在的 Z-APP 對 Z-MGMT 發 raw log query 必須失敗」。真正的實測是
range 網路層的 T3（見 deploy/range-target/RUNBOOK-attack-chain.md 之外的 range 契約）。
這裡守的是它的結構前提：**Console 自己沒有任何 raw-query 程式路徑** —— 它只消費 Evaluation
API 已判好的結果與 source registry 的唯讀模型，不碰 DB、不碰 Loki、不碰 raw event store。

若哪天有人在 console 套件裡 import 了 psycopg 或某個 raw store，這條會紅 —— 那正是權限
邊界從程式面被打開的時刻。
"""

from pathlib import Path

CONSOLE_DIR = Path(__file__).resolve().parents[2] / "src" / "purple" / "console"

FORBIDDEN = (
    "psycopg",
    "LokiBackend",
    "CoreEventStore",
    "AlertRecordStore",
    "from purple.store",
    "import purple.store",
    "connect(",
)


def test_console_package_has_no_raw_query_path():
    for path in CONSOLE_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in source, f"{path.name} 出現 raw-query 相依：{token}"
