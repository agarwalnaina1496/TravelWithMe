"""PostgresTripRepository user-account persistence (TWM-178)."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest

from twm.persistence.contracts import DuplicateEmailError
from twm.persistence.postgres import PostgresTripRepository


class FakeUsersPool:
    def __init__(self):
        self.users: dict[str, dict] = {}

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if q.startswith("INSERT INTO twm_app.users"):
            email, password_hash = args
            if email in self.users:
                raise asyncpg.UniqueViolationError("duplicate key value violates unique constraint")
            row = {"id": uuid4(), "email": email, "password_hash": password_hash, "created_at": datetime.now(timezone.utc)}
            self.users[email] = row
            return row
        if q.startswith("SELECT * FROM twm_app.users WHERE email=$1"):
            (email,) = args
            return self.users.get(email)
        if q.startswith("SELECT * FROM twm_app.users WHERE id=$1"):
            (user_id,) = args
            return next((row for row in self.users.values() if row["id"] == user_id), None)
        raise AssertionError(f"FakeUsersPool: unhandled query: {q}")


def _repository() -> tuple[PostgresTripRepository, FakeUsersPool]:
    pool = FakeUsersPool()
    return PostgresTripRepository(pool=pool, schema="twm_app"), pool


def test_create_user_stores_the_hash_and_returns_the_record() -> None:
    repository, _ = _repository()

    user = asyncio.run(repository.create_user("traveler@example.com", "hashed-value"))

    assert user.email == "traveler@example.com"
    assert user.password_hash == "hashed-value"


def test_create_user_raises_on_duplicate_email() -> None:
    repository, _ = _repository()
    asyncio.run(repository.create_user("traveler@example.com", "hashed-value"))

    with pytest.raises(DuplicateEmailError):
        asyncio.run(repository.create_user("traveler@example.com", "another-hash"))


def test_get_user_by_email_returns_none_when_unknown() -> None:
    repository, _ = _repository()

    assert asyncio.run(repository.get_user_by_email("nobody@example.com")) is None


def test_get_user_by_id_resolves_a_known_user() -> None:
    repository, _ = _repository()
    created = asyncio.run(repository.create_user("traveler@example.com", "hashed-value"))

    resolved = asyncio.run(repository.get_user_by_id(created.id))

    assert resolved == created


def test_get_user_by_id_returns_none_when_unknown() -> None:
    repository, _ = _repository()

    assert asyncio.run(repository.get_user_by_id(uuid4())) is None
