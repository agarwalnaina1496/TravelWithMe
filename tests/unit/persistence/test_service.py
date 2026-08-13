"""Guest-session identity logic in TripPersistenceService."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

from fastapi import Response

from twm.persistence.contracts import GuestSession, TripRepository
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings


class FakeTripRepository(TripRepository):
    """Minimal in-memory TripRepository; only guest-session methods are exercised here."""

    def __init__(self):
        self.guests: dict[str, GuestSession] = {}
        self.resolve_calls: list[tuple[str, int]] = []
        self.create_calls: list[tuple[str, int]] = []

    async def resolve_guest(self, token_hash: str, lifetime_days: int):
        self.resolve_calls.append((token_hash, lifetime_days))
        return self.guests.get(token_hash)

    async def create_guest(self, token_hash: str, lifetime_days: int):
        self.create_calls.append((token_hash, lifetime_days))
        guest = GuestSession(uuid4(), datetime.now(timezone.utc) + timedelta(days=lifetime_days))
        self.guests[token_hash] = guest
        return guest

    async def list_trips(self, guest_id):
        raise NotImplementedError

    async def create_trip(self, guest_id, title, product_mode, trip_state, ui_state):
        raise NotImplementedError

    async def get_trip(self, guest_id, trip_id):
        raise NotImplementedError

    async def replace_trip(self, guest_id, trip_id, expected_version, trip_state, ui_state):
        raise NotImplementedError

    async def rename_trip(self, guest_id, trip_id, expected_version, title):
        raise NotImplementedError

    async def update_ui_state(self, guest_id, trip_id, expected_version, ui_state):
        raise NotImplementedError

    async def get_command(self, guest_id, trip_id, idempotency_key):
        raise NotImplementedError

    async def get_latest_recommendation(self, guest_id, trip_id):
        raise NotImplementedError

    async def list_itinerary_versions(self, guest_id, trip_id):
        raise NotImplementedError

    async def commit_command(self, guest_id, trip_id, expected_version, idempotency_key, request_hash, trip_state, response_trip_state, response, new_recommendation=None, new_itinerary_version=None):
        raise NotImplementedError


def _request(cookie_value: str | None) -> Mock:
    request = Mock()
    request.cookies = {"twm_guest": cookie_value} if cookie_value else {}
    return request


def _service(repository: FakeTripRepository, **settings_overrides) -> TripPersistenceService:
    settings = DatabaseSettings(url=None, **settings_overrides)
    return TripPersistenceService(repository=repository, settings=settings)


def test_guest_resolves_existing_session_from_cookie() -> None:
    repository = FakeTripRepository()
    service = _service(repository)
    token_hash = hashlib.sha256(b"known-token").hexdigest()
    repository.guests[token_hash] = GuestSession(uuid4(), datetime.now(timezone.utc) + timedelta(days=1))

    guest = asyncio.run(service.guest(_request("known-token"), Response()))

    assert guest is repository.guests[token_hash]
    assert repository.create_calls == []


def test_guest_creates_new_session_when_no_cookie_present() -> None:
    repository = FakeTripRepository()
    service = _service(repository)

    asyncio.run(service.guest(_request(None), Response()))

    assert len(repository.create_calls) == 1
    assert repository.resolve_calls == []


def test_guest_creates_new_session_when_cookie_is_unresolvable() -> None:
    repository = FakeTripRepository()
    service = _service(repository)

    guest = asyncio.run(service.guest(_request("stale-token"), Response()))

    assert len(repository.create_calls) == 1
    assert repository.resolve_calls == [(hashlib.sha256(b"stale-token").hexdigest(), 180)]
    assert guest.id in {g.id for g in repository.guests.values()}


def test_guest_sets_a_secure_httponly_cookie_by_default() -> None:
    repository = FakeTripRepository()
    service = _service(repository)
    response = Response()

    asyncio.run(service.guest(_request(None), response))

    set_cookie_header = response.headers["set-cookie"]
    assert "twm_guest=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header
    assert "Secure" in set_cookie_header
    assert "samesite=lax" in set_cookie_header.lower()
    assert f"Max-Age={180 * 24 * 60 * 60}" in set_cookie_header


def test_guest_cookie_secure_flag_follows_settings() -> None:
    repository = FakeTripRepository()
    service = _service(repository, guest_cookie_secure=False)
    response = Response()

    asyncio.run(service.guest(_request(None), response))

    assert "Secure" not in response.headers["set-cookie"]
