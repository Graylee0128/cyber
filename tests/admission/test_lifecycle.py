from __future__ import annotations

from datetime import UTC, datetime, timedelta

from admission.service import AdmissionService
from admission.store.pool import PoolConfigStore
from admission.store.seats import SeatStore


class RangeFake:
    def __init__(self):
        self.published = []
        self.revoked = []

    def publish_player(self, **player):
        self.published.append(player)

    def revoke_player(self, exercise_id, player_id):
        self.revoked.append(player_id)


class AlertFake:
    def __init__(self): self.alerts = []
    def notify(self, **alert): self.alerts.append(alert)


def test_ready_publication_is_idempotent_and_release_revokes(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    fake = RangeFake()
    service = AdmissionService(pg_connection, publisher=fake)
    claim = service.allocate("EX", "red")
    assert fake.published == []
    endpoints = [{"terminal": "main", "host": "10.167.30.11", "port": 7681}]
    assert service.ready(claim["seat_id"], endpoints)
    assert service.ready(claim["seat_id"], endpoints)
    assert [x["player_id"] for x in fake.published] == [claim["player_id"]]
    service.release(claim["seat_id"], actor="instructor")
    assert fake.revoked == [claim["player_id"]]


def test_rebind_preserves_player_and_both_operations_are_audited(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    service = AdmissionService(pg_connection, publisher=RangeFake())
    claim = service.allocate("EX", "red")
    old = service.bind_session(claim["seat_id"])
    assert service.resolve_session("forged-token") is None
    new = service.rebind(claim["seat_id"], actor="teacher")
    assert new != old
    assert service.resolve_session(old) is None
    assert service.resolve_session(new)["player_id"] == claim["player_id"]
    service.release(claim["seat_id"], actor="teacher")
    assert service.resolve_session(new) is None
    rows = pg_connection.execute("SELECT action, actor FROM admission_audit ORDER BY audit_id").fetchall()
    assert rows == [("rebind", "teacher"), ("release", "teacher")]


def test_timeout_first_fails_for_retry_second_releases_and_alerts(pg_connection):
    PoolConfigStore(pg_connection).set_caps("EX", 1, 0)
    alerts, publisher = AlertFake(), RangeFake()
    service = AdmissionService(pg_connection, publisher=publisher, alerter=alerts)
    claim = service.allocate("EX", "red")
    old = datetime.now(UTC) - timedelta(seconds=11)
    pg_connection.execute("UPDATE seat SET requested_at=%s WHERE seat_id=%s", (old, claim["seat_id"]))
    assert service.expire_requests(10) == {"retry": 1, "released": 0}
    assert SeatStore(pg_connection).retry_failed(claim["seat_id"])
    pg_connection.execute("UPDATE seat SET requested_at=%s WHERE seat_id=%s", (old, claim["seat_id"]))
    assert service.expire_requests(10) == {"retry": 0, "released": 1}
    assert SeatStore(pg_connection).get(claim["seat_id"])["player_id"] is None
    assert publisher.revoked == [claim["player_id"]]
    assert len(alerts.alerts) == 1
