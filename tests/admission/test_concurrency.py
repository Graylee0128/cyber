from concurrent.futures import ThreadPoolExecutor

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
