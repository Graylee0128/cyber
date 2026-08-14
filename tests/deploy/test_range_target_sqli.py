"""靶機 VM /product 的 SQLi 偵測輸入（票 #44 攻擊面 / #90 Phase 4）。

Grafana 的 SQLInjectionBurstTarget 規則數 `sqli_suspected=true` 的筆數才 firing，
所以 app 必須對可注入的 id 記 `sqli_suspected=True`、對正常 id 記 False。這條測試
守住那個布林 —— 規則寫得再對，app 不標記就永遠不 firing。
"""

import importlib.util
from pathlib import Path

_APP_PATH = Path(__file__).resolve().parents[2] / "deploy" / "range-target" / "app.py"
_spec = importlib.util.spec_from_file_location("range_target_app", _APP_PATH)
range_target_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(range_target_app)

_looks_like_sqli = range_target_app._looks_like_sqli


class TestSqliClassification:
    def test_union_injection_is_flagged(self):
        payload = "0 UNION SELECT id,service,username,password FROM credentials"
        assert _looks_like_sqli(payload) is True

    def test_boolean_injection_is_flagged(self):
        assert _looks_like_sqli("1' OR '1'='1") is True

    def test_harness_marker_comment_is_flagged(self):
        # harness.attacker.make_marker 產的 `-- purple:.. action:..` 尾綴。
        assert _looks_like_sqli("1 -- purple:abc123 action:sqli-target-44-run-01") is True

    def test_plain_numeric_id_is_not_flagged(self):
        assert _looks_like_sqli("42") is False

    def test_empty_id_is_not_flagged(self):
        assert _looks_like_sqli("") is False
