from datetime import UTC, datetime

from admission.credentials import onsite_code, verify_onsite_code


def test_onsite_hmac_uses_60_second_bucket():
    now = datetime(2026, 8, 14, 1, 2, 3, tzinfo=UTC)
    code = onsite_code("secret", "EX-1", now)
    assert verify_onsite_code(code, "secret", "EX-1", now)
    assert not verify_onsite_code("forged", "secret", "EX-1", now)
    assert not verify_onsite_code(code, "secret", "EX-1", datetime(2026, 8, 14, 1, 3, 3, tzinfo=UTC))
