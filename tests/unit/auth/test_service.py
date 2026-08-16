"""AuthService signup/login/current_user orchestration (TWM-178)."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

from fastapi import Response

from twm.auth.security import verify_password
from twm.auth.service import AuthService, InvalidCredentialsError
from twm.auth.settings import AuthSettings
from twm.persistence.contracts import DuplicateEmailError, User


class FakeUserRepository:
    def __init__(self):
        self.users_by_email: dict[str, User] = {}

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


def _service(**overrides) -> AuthService:
    settings = AuthSettings(jwt_secret="test-secret", jwt_expiry_days=1, **overrides)
    return AuthService(repository=FakeUserRepository(), settings=settings, logger=Mock())


def test_signup_stores_a_hashed_password() -> None:
    service = _service()

    user = asyncio.run(service.signup("Traveler@Example.com", "hunter22"))

    assert user.email == "traveler@example.com"
    assert user.password_hash != "hunter22"
    assert verify_password("hunter22", user.password_hash)


def test_signup_rejects_a_duplicate_email() -> None:
    service = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22"))

    try:
        asyncio.run(service.signup("traveler@example.com", "another-password"))
        assert False, "expected DuplicateEmailError"
    except DuplicateEmailError:
        pass


def test_login_issues_a_jwt_cookie_on_success() -> None:
    service = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22"))
    response = Response()

    user = asyncio.run(service.login("traveler@example.com", "hunter22", response))

    assert user.email == "traveler@example.com"
    set_cookie_header = response.headers["set-cookie"]
    assert "twm_auth=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header


def test_login_rejects_the_wrong_password() -> None:
    service = _service()
    asyncio.run(service.signup("traveler@example.com", "hunter22"))
    response = Response()

    try:
        asyncio.run(service.login("traveler@example.com", "wrong-password", response))
        assert False, "expected InvalidCredentialsError"
    except InvalidCredentialsError:
        pass
    assert "set-cookie" not in response.headers


def test_login_rejects_an_unknown_email() -> None:
    service = _service()
    response = Response()

    try:
        asyncio.run(service.login("nobody@example.com", "hunter22", response))
        assert False, "expected InvalidCredentialsError"
    except InvalidCredentialsError:
        pass


def test_current_user_resolves_from_a_valid_cookie() -> None:
    service = _service()
    created = asyncio.run(service.signup("traveler@example.com", "hunter22"))
    login_response = Response()
    asyncio.run(service.login("traveler@example.com", "hunter22", login_response))
    token = login_response.headers["set-cookie"].split("twm_auth=")[1].split(";")[0]
    request = Mock()
    request.cookies = {"twm_auth": token}

    resolved = asyncio.run(service.current_user(request))

    assert resolved.id == created.id


def test_current_user_returns_none_when_no_cookie_present() -> None:
    service = _service()
    request = Mock()
    request.cookies = {}

    assert asyncio.run(service.current_user(request)) is None


def test_current_user_returns_none_for_an_invalid_token() -> None:
    service = _service()
    request = Mock()
    request.cookies = {"twm_auth": "not-a-real-jwt"}

    assert asyncio.run(service.current_user(request)) is None
