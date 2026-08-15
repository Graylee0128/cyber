from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest

from admission.service import AdmissionService, TeamFull
from admission.store.db import connect
from admission.store.pool import PoolConfigStore
from admission.store.seats import SeatStore


def test_more_claimants_than_blue_seats_get_exact_capacity_with_unique_ids(pg_connection):
    exercise_id, capacity, claimants = "EX-CONCURRENT", 4, 12
    PoolConfigStore(pg_connection).set_caps(exercise_id, 0, capacity)
    SeatStore(pg_connection).bulk_build_blue_seats(exercise_id, capacity)

    def attempt(_):
        separate = connect()
        try:
            try:
                return AdmissionService(separate).allocate(exercise_id, "blue")
            except TeamFull:
                return None
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=claimants) as workers:
        results = list(workers.map(attempt, range(claimants)))
    successes = [result for result in results if result is not None]
    assert len(successes) == capacity
    assert len({result["seat_id"] for result in successes}) == capacity
    assert len({result["player_id"] for result in successes}) == capacity


def test_locked_blue_pool_rejects_self_service_claim(pg_connection):
    pools = PoolConfigStore(pg_connection)
    pools.set_caps("EX-LOCKED", 0, 1)
    SeatStore(pg_connection).bulk_build_blue_seats("EX-LOCKED", 1)
    assert pools.lock("EX-LOCKED")

    with pytest.raises(TeamFull):
        AdmissionService(pg_connection).allocate("EX-LOCKED", "blue")


def test_concurrent_lock_and_build_creates_exactly_the_configured_cap(pg_connection):
    exercise_id, capacity = "EX-LOCK-BUILD", 5
    PoolConfigStore(pg_connection).set_caps(exercise_id, 0, capacity)

    def lock_and_build(_):
        separate = connect()
        try:
            return PoolConfigStore(separate).lock_and_build_blue(exercise_id)
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lock_and_build, range(2)))

    count = pg_connection.execute(
        "SELECT count(*) FROM seat WHERE exercise_id=%s AND team='blue'",
        (exercise_id,),
    ).fetchone()[0]
    assert sorted(results) == [False, True]
    assert count == capacity


class ThreadSafeRangeFake:
    def __init__(self):
        self.revoked = []
        self._lock = Lock()

    def publish_player(self, **_player): pass

    def revoke_player(self, exercise_id, player_id):
        with self._lock:
            self.revoked.append((exercise_id, player_id))

    def prepare(self, scenario_id):
        return {"exercise_id": "ex-fake", "scenario_id": scenario_id, "state": "prepared"}


class ThreadSafeAlertFake:
    def __init__(self):
        self.alerts = []
        self._lock = Lock()

    def notify(self, **alert):
        with self._lock:
            self.alerts.append(alert)


def test_concurrent_timeout_processors_release_and_alert_only_once(pg_connection):
    exercise_id = "EX-TIMEOUT-RACE"
    PoolConfigStore(pg_connection).set_caps(exercise_id, 1, 0)
    claim = AdmissionService(pg_connection).allocate(exercise_id, "red")
    old = datetime.now(UTC) - timedelta(seconds=11)
    pg_connection.execute(
        "UPDATE seat SET retry_count=1, requested_at=%s WHERE seat_id=%s",
        (old, claim["seat_id"]),
    )
    publisher, alerter = ThreadSafeRangeFake(), ThreadSafeAlertFake()

    def expire(_):
        separate = connect()
        try:
            return AdmissionService(separate, publisher=publisher, alerter=alerter).expire_requests(10)
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(expire, range(2)))

    assert sum(result["released"] for result in results) == 1
    assert publisher.revoked == [(exercise_id, claim["player_id"])]
    assert len(alerter.alerts) == 1


def test_timeout_processors_never_downgrade_a_ready_seat(pg_connection):
    exercise_id = "EX-READY-RACE"
    PoolConfigStore(pg_connection).set_caps(exercise_id, 1, 0)
    claim = AdmissionService(pg_connection).allocate(exercise_id, "red")
    old = datetime.now(UTC) - timedelta(seconds=11)
    pg_connection.execute(
        "UPDATE seat SET requested_at=%s WHERE seat_id=%s", (old, claim["seat_id"])
    )
    endpoints = [{"terminal": "main", "host": "10.167.30.11", "port": 7681}]
    assert AdmissionService(pg_connection).ready(claim["seat_id"], endpoints)

    def expire(_):
        separate = connect()
        try:
            return AdmissionService(separate).expire_requests(10)
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(expire, range(2)))

    assert results == [{"retry": 0, "released": 0}] * 2
    assert SeatStore(pg_connection).get(claim["seat_id"])["state"] == "ready"


def test_concurrent_red_cap_reduction_and_claim_never_exceeds_committed_cap(pg_connection):
    exercise_id = "EX-RED-CAP-RACE"
    pools = PoolConfigStore(pg_connection)
    pools.set_caps(exercise_id, 2, 0)
    AdmissionService(pg_connection).allocate(exercise_id, "red")

    def reduce_cap():
        separate = connect()
        try:
            try:
                PoolConfigStore(separate).set_caps_and_prepare_blue(exercise_id, 1, 0)
                return "reduced"
            except ValueError:
                return "rejected"
        finally:
            separate.close()

    def claim_red():
        separate = connect()
        try:
            try:
                AdmissionService(separate).allocate(exercise_id, "red")
                return "claimed"
            except TeamFull:
                return "full"
        finally:
            separate.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [workers.submit(reduce_cap), workers.submit(claim_red)]
        outcomes = [future.result() for future in results]

    cfg = pools.get(exercise_id)
    count = pg_connection.execute(
        "SELECT count(*) FROM seat WHERE exercise_id=%s AND team='red' AND state<>'released'",
        (exercise_id,),
    ).fetchone()[0]
    assert count <= cfg.red_cap
    assert outcomes in (["reduced", "full"], ["rejected", "claimed"])
