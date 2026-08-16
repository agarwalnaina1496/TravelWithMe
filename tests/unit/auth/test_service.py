"""AuthService signup/login/current_user/claim orchestration (TWM-178/179)."""

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

from fastapi import Response

from twm.auth.security import verify_password
from twm.auth.service import AuthService, InvalidCredentialsError
from twm.auth.settings import AuthSettings
from twm.persistence.contracts import DuplicateEmailError, GuestSession, User
from twm.persistence.settings import DatabaseSettings


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeUserRepository:
    def __init__(self):
        self.users_by_email: dict[str, User] = {}
        self.guests_by_hash: dict[str, GuestSession] = {}
        self.trips_by_guest: dict = {}  # guest_session_id -> trip_count
        self.claim_calls: list[tuple] = []

    async def create_user(self, email: str, password_hash: str) -> User:
        if email in self.users_by_email:
            raise DuplicateEmailError(email)
        user = User(id=uuid4(), email=email, password_hash=password_hash, created_at=datetime.now(timezone.utc))
        self.users_by_email[email] = user
        return user

    async def get_user_by_email(self, email: str) -> User | None:
        return self.users_by_email.get(email)

    async def get_user_by_id(self, user_id):
        return next((u for u in self.users_by_email.values() if u.id == user_id), None)

    async def resolve_guest(self, token_hash, lifetime_days):
        guest = self.guests_by_hash.get(token_hash)
        if guest and guest.expires_at > datetime.now(timezone.utc):
            return guest
        return None

    async def claim_guest_trips(self, guest_session_id, user_id) -> int:
        self.claim_calls.append((guest_session_id, user_id))
        return self.trips_by_guest.pop(guest_session_id, 0)

    def seed_guest(self, token: str, *, trip_count: int = 0, expired: bool = False) -> GuestSession:
        guest = GuestSession(
            uuid4(),
            datetime.now(timezone.utc) + (timedelta(days=-1) if expired else timedelta(days=180)),
        )
        self.guests_by_hash[_hash(token)] = guest
        if trip_count:
            self.trips_by_guest[guest.id] = trip_count
        return guest


def _request(cookie_value: str | None = None) -> Mock:
    request = Mock()
    request.cookies = {"twm_guest": cookie_value} if cookie_value else {}
    return request


def _service(repository: FakeUserRepository | None = None, **overrides) -> tuple[AuthService, FakeUserRepository]:
    repository = repository or FakeUserRepository()
    settings = AuthSettings(jwt_secret="test-secret-at-least-32-bytes-long!!", jwt_expiry_days=1)
    database_settings = DatabaseSettings(url=None, **overrides)
    return AuthService(repository=repository, settings=settings, database_settings=database_settings, logger=Mock()), repository


def test_signup_stores_a_hashed_password() -> None:
    service, _ = _service()

    result = asyncio.run(service.signup("Traveler@Example.com", "hunter22", _request()))

    assert result.user.email == "traveler@example.com"
    assert result.user.password_hash != "hunter22"
    assert verify_password("hunter22", result.user.password_hash)


def test_signup_rejects_a_duplicate_email() -> None:
    service, _ = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))

    try:
        asyncio.run(service.signup("traveler@example.com", "another-password", _request()))
        assert False, "expected DuplicateEmailError"
    except DuplicateEmailError:
        pass


def test_login_issues_a_jwt_cookie_on_success() -> None:
    service, _ = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))
    response = Response()

    result = asyncio.run(service.login("traveler@example.com", "hunter22", _request(), response))

    assert result.user.email == "traveler@example.com"
    set_cookie_header = response.headers["set-cookie"]
    assert "twm_auth=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header


def test_login_rejects_the_wrong_password() -> None:
    service, _ = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))
    response = Response()

    try:
        asyncio.run(service.login("traveler@example.com", "wrong-password", _request(), response))
        assert False, "expected InvalidCredentialsError"
    except InvalidCredentialsError:
        pass
    assert "set-cookie" not in response.headers


def test_login_rejects_an_unknown_email() -> None:
    service, _ = _service()
    response = Response()

    try:
        asyncio.run(service.login("nobody@example.com", "hunter22", _request(), response))
        assert False, "expected InvalidCredentialsError"
    except InvalidCredentialsError:
        pass


def test_login_pays_the_same_bcrypt_verification_cost_for_an_unknown_email(monkeypatch) -> None:
    """An unregistered email must not short-circuit before verify_password
    runs — otherwise response timing discloses which emails are registered."""
    service, _ = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))
    calls: list[str] = []
    original_verify_password = __import__("twm.auth.service", fromlist=["verify_password"]).verify_password

    def spy(password, password_hash):
        calls.append(password_hash)
        return original_verify_password(password, password_hash)

    monkeypatch.setattr("twm.auth.service.verify_password", spy)

    try:
        asyncio.run(service.login("nobody@example.com", "hunter22", _request(), Response()))
    except InvalidCredentialsError:
        pass

    assert len(calls) == 1


def test_current_user_resolves_from_a_valid_cookie() -> None:
    service, _ = _service()
    signup_result = asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))
    login_response = Response()
    asyncio.run(service.login("traveler@example.com", "hunter22", _request(), login_response))
    token = login_response.headers["set-cookie"].split("twm_auth=")[1].split(";")[0]
    request = Mock()
    request.cookies = {"twm_auth": token}

    resolved = asyncio.run(service.current_user(request))

    assert resolved.id == signup_result.user.id


def test_current_user_returns_none_when_no_cookie_present() -> None:
    service, _ = _service()
    request = Mock()
    request.cookies = {}

    assert asyncio.run(service.current_user(request)) is None


def test_current_user_returns_none_for_an_invalid_token() -> None:
    service, _ = _service()
    request = Mock()
    request.cookies = {"twm_auth": "not-a-real-jwt"}

    assert asyncio.run(service.current_user(request)) is None


def test_signup_claims_guest_trips_when_a_live_guest_cookie_is_present() -> None:
    service, repository = _service()
    guest = repository.seed_guest("guest-token", trip_count=3)

    result = asyncio.run(service.signup("traveler@example.com", "hunter22", _request("guest-token")))

    assert result.claimed_trip_count == 3
    assert repository.claim_calls == [(guest.id, result.user.id)]


def test_signup_with_no_guest_cookie_claims_nothing() -> None:
    service, repository = _service()

    result = asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))

    assert result.claimed_trip_count == 0
    assert repository.claim_calls == []


def test_signup_with_zero_guest_trips_is_a_clean_no_op() -> None:
    service, repository = _service()
    repository.seed_guest("guest-token", trip_count=0)

    result = asyncio.run(service.signup("traveler@example.com", "hunter22", _request("guest-token")))

    assert result.claimed_trip_count == 0


def test_signup_with_an_expired_guest_cookie_does_not_crash() -> None:
    service, repository = _service()
    repository.seed_guest("guest-token", trip_count=2, expired=True)

    result = asyncio.run(service.signup("traveler@example.com", "hunter22", _request("guest-token")))

    assert result.claimed_trip_count == 0
    assert repository.claim_calls == []


def test_login_claims_guest_trips_on_success() -> None:
    service, repository = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))
    guest = repository.seed_guest("guest-token", trip_count=1)

    result = asyncio.run(service.login("traveler@example.com", "hunter22", _request("guest-token"), Response()))

    assert result.claimed_trip_count == 1
    assert repository.claim_calls[-1] == (guest.id, result.user.id)


def test_second_login_from_the_same_browser_does_not_re_claim() -> None:
    """Idempotency: the repository's own WHERE-user_id-IS-NULL guard makes a
    repeat claim a no-op — this just confirms the service doesn't special-case
    or error on it either."""
    service, repository = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22", _request()))
    repository.seed_guest("guest-token", trip_count=2)

    first = asyncio.run(service.login("traveler@example.com", "hunter22", _request("guest-token"), Response()))
    second = asyncio.run(service.login("traveler@example.com", "hunter22", _request("guest-token"), Response()))

    assert first.claimed_trip_count == 2
    assert second.claimed_trip_count == 0
