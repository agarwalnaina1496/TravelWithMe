"""PostgresTripRepository.claim_guest_trips (TWM-179)."""

import asyncio
from uuid import uuid4

from twm.persistence.postgres import PostgresTripRepository


class FakeClaimPool:
    def __init__(self):
        self.trips: dict = {}

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        assert q.startswith("UPDATE twm_app.trips SET user_id=$2")
        guest_session_id, user_id = args
        count = 0
        for trip in self.trips.values():
            if trip["guest_session_id"] == guest_session_id and trip["user_id"] is None:
                trip["user_id"] = user_id
                count += 1
        return f"UPDATE {count}"


def _repository() -> tuple[PostgresTripRepository, FakeClaimPool]:
    pool = FakeClaimPool()
    return PostgresTripRepository(pool=pool, schema="twm_app"), pool


def _seed(pool: FakeClaimPool, guest_session_id, *, user_id=None):
    trip_id = uuid4()
    pool.trips[trip_id] = {"guest_session_id": guest_session_id, "user_id": user_id}
    return trip_id


def test_claim_guest_trips_reassigns_every_unclaimed_trip_for_the_guest_session() -> None:
    repository, pool = _repository()
    guest_id = uuid4()
    user_id = uuid4()
    _seed(pool, guest_id)
    _seed(pool, guest_id)
    other_guest_trip = _seed(pool, uuid4())

    claimed = asyncio.run(repository.claim_guest_trips(guest_id, user_id))

    assert claimed == 2
    assert all(trip["user_id"] == user_id for tid, trip in pool.trips.items() if tid != other_guest_trip)
    assert pool.trips[other_guest_trip]["user_id"] is None


def test_claim_guest_trips_is_a_no_op_when_the_guest_has_no_trips() -> None:
    repository, pool = _repository()

    claimed = asyncio.run(repository.claim_guest_trips(uuid4(), uuid4()))

    assert claimed == 0


def test_claim_guest_trips_does_not_re_claim_already_claimed_trips() -> None:
    """Idempotency: a second login must not touch trips already owned by
    someone (including the same user), matching the WHERE user_id IS NULL
    guard."""
    repository, pool = _repository()
    guest_id = uuid4()
    first_user = uuid4()
    already_claimed = _seed(pool, guest_id, user_id=first_user)

    claimed = asyncio.run(repository.claim_guest_trips(guest_id, uuid4()))

    assert claimed == 0
    assert pool.trips[already_claimed]["user_id"] == first_user
