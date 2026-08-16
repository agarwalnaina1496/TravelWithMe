"""PostgresTripRepository.claim_guest_trips (TWM-179)."""

import asyncio
from uuid import uuid4

from twm.persistence.postgres import PostgresTripRepository


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Connection:
    def __init__(self, pool: "FakeClaimPool"):
        self.pool = pool

    def transaction(self):
        return _Transaction()

    async def execute(self, query: str, *args):
        return await self.pool.execute(query, *args)


class _Acquire:
    def __init__(self, pool: "FakeClaimPool"):
        self.pool = pool

    async def __aenter__(self):
        return _Connection(self.pool)

    async def __aexit__(self, *exc):
        return False


class FakeClaimPool:
    def __init__(self):
        self.trips: dict = {}
        self.trip_commands: dict = {}

    def acquire(self):
        return _Acquire(self)

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        guest_session_id, user_id = args
        if q.startswith("UPDATE twm_app.trips SET user_id=$2"):
            count = 0
            for trip in self.trips.values():
                if trip["guest_session_id"] == guest_session_id and trip["user_id"] is None:
                    trip["user_id"] = user_id
                    count += 1
            return f"UPDATE {count}"
        if q.startswith("UPDATE twm_app.trip_commands SET user_id=$2"):
            count = 0
            for command in self.trip_commands.values():
                if command["guest_session_id"] == guest_session_id and command["user_id"] is None:
                    command["user_id"] = user_id
                    count += 1
            return f"UPDATE {count}"
        raise AssertionError(f"FakeClaimPool: unhandled query: {q}")


def _repository() -> tuple[PostgresTripRepository, FakeClaimPool]:
    pool = FakeClaimPool()
    return PostgresTripRepository(pool=pool, schema="twm_app"), pool


def _seed_trip(pool: FakeClaimPool, guest_session_id, *, user_id=None):
    trip_id = uuid4()
    pool.trips[trip_id] = {"guest_session_id": guest_session_id, "user_id": user_id}
    return trip_id


def _seed_command(pool: FakeClaimPool, guest_session_id, *, user_id=None):
    key = uuid4()
    pool.trip_commands[key] = {"guest_session_id": guest_session_id, "user_id": user_id}
    return key


def test_claim_guest_trips_reassigns_every_unclaimed_trip_for_the_guest_session() -> None:
    repository, pool = _repository()
    guest_id = uuid4()
    user_id = uuid4()
    _seed_trip(pool, guest_id)
    _seed_trip(pool, guest_id)
    other_guest_trip = _seed_trip(pool, uuid4())

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
    already_claimed = _seed_trip(pool, guest_id, user_id=first_user)

    claimed = asyncio.run(repository.claim_guest_trips(guest_id, uuid4()))

    assert claimed == 0
    assert pool.trips[already_claimed]["user_id"] == first_user


def test_claim_guest_trips_also_reassigns_trip_commands_for_the_same_guest_session() -> None:
    """A pre-claim idempotency key resent after login must still replay
    instead of re-executing — get_command()/commit_command() resolve by
    user_id once authenticated, so a stale guest_session_id=..., user_id=NULL
    trip_commands row would otherwise be invisible to that lookup."""
    repository, pool = _repository()
    guest_id = uuid4()
    user_id = uuid4()
    _seed_trip(pool, guest_id)
    claimed_command = _seed_command(pool, guest_id)
    other_guest_command = _seed_command(pool, uuid4())

    asyncio.run(repository.claim_guest_trips(guest_id, user_id))

    assert pool.trip_commands[claimed_command]["user_id"] == user_id
    assert pool.trip_commands[other_guest_command]["user_id"] is None


def test_claim_guest_trips_does_not_re_claim_already_claimed_trip_commands() -> None:
    repository, pool = _repository()
    guest_id = uuid4()
    first_user = uuid4()
    already_claimed = _seed_command(pool, guest_id, user_id=first_user)

    asyncio.run(repository.claim_guest_trips(guest_id, uuid4()))

    assert pool.trip_commands[already_claimed]["user_id"] == first_user
