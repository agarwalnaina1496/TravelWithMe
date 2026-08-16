"""API coverage for account signup and login (TWM-178)."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from twm.auth import AuthService, AuthSettings
from twm.dependencies import get_auth_service
from twm.main import app
from twm.persistence.contracts import DuplicateEmailError, User
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


class MemoryUserRepository:
    def __init__(self):
        self.users_by_email: dict[str, User] = {}

    async def create_user(self, email, password_hash):
        if email in self.users_by_email:
            raise DuplicateEmailError(email)
        user = User(id=uuid4(), email=email, password_hash=password_hash, created_at=datetime.now(timezone.utc))
        self.users_by_email[email] = user
        return user

    async def get_user_by_email(self, email):
        return self.users_by_email.get(email)

    async def get_user_by_id(self, user_id):
        return next((u for u in self.users_by_email.values() if u.id == user_id), None)


def _logger() -> TelemetryLogger:
    return TelemetryLogger(
        settings=TelemetrySettings(enabled=True, environment="test", service="twm-test", payload_mode=PayloadMode.OFF, max_field_size=4096),
        sink=InMemorySink(),
    )


def _auth_service(repository: MemoryUserRepository | None = None) -> AuthService:
    return AuthService(
        repository=repository or MemoryUserRepository(),
        settings=AuthSettings(jwt_secret="test-secret-at-least-32-bytes-long!!", jwt_cookie_secure=False, jwt_expiry_days=1),
        logger=_logger(),
    )


@pytest.fixture
def api_client() -> TestClient:
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_signup_creates_an_account(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "traveler@example.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_signup_rejects_a_duplicate_email(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})

    response = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "another-pass"})

    assert response.status_code == 409


def test_signup_rejects_a_malformed_email(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/signup", json={"email": "not-an-email", "password": "hunter22!!"})

    assert response.status_code == 422


def test_login_succeeds_with_correct_credentials_and_sets_a_cookie(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})

    response = api_client.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})

    assert response.status_code == 200
    assert response.json()["email"] == "traveler@example.com"
    assert "twm_auth" in response.cookies


def test_login_rejects_the_wrong_password(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})

    response = api_client.post("/auth/login", json={"email": "traveler@example.com", "password": "wrong-password"})

    assert response.status_code == 401
    assert "twm_auth" not in response.cookies


def test_login_rejects_an_unregistered_email(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/login", json={"email": "nobody@example.com", "password": "hunter22!!"})

    assert response.status_code == 401
