"""API coverage for account signup, login, and guest-trip claim (TWM-178/179)."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from twm.auth import AuthService, AuthSettings
from twm.dependencies import get_auth_service, get_trip_persistence
from twm.main import app
from twm.persistence.contracts import DuplicateEmailError, GuestSession, TripRecord, User
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


def _owned_by(trip: TripRecord, owner) -> bool:
    if owner.user_id is not None:
        return trip.user_id == owner.user_id
    return trip.guest_session_id == owner.guest_session_id and trip.user_id is None


class MemoryUserRepository:
    """Implements both the auth and trip surfaces of TripRepository, like the
    real Postgres repository does — needed so signup/login can claim trips
    created through the same shared repository (TWM-179)."""

    def __init__(self):
        self.users_by_email: dict[str, User] = {}
        self.guests_by_hash: dict[str, GuestSession] = {}
        self.trips: dict = {}

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

    async def resolve_guest(self, token_hash, lifetime_days):
        guest = self.guests_by_hash.get(token_hash)
        if guest and guest.expires_at > datetime.now(timezone.utc):
            return guest
        return None

    async def create_guest(self, token_hash, lifetime_days):
        guest = GuestSession(uuid4(), datetime.now(timezone.utc) + timedelta(days=lifetime_days))
        self.guests_by_hash[token_hash] = guest
        return guest

    async def claim_guest_trips(self, guest_session_id, user_id):
        claimed = 0
        for trip_id, trip in list(self.trips.items()):
            if trip.guest_session_id == guest_session_id and trip.user_id is None:
                self.trips[trip_id] = _replace_owner(trip, user_id)
                claimed += 1
        return claimed

    async def list_trips(self, owner):
        return [trip for trip in self.trips.values() if _owned_by(trip, owner)]

    async def create_trip(self, guest_id, user_id, title, product_mode, trip_state, ui_state):
        now = datetime.now(timezone.utc)
        trip = TripRecord(uuid4(), guest_id, user_id, title, product_mode, trip_state, ui_state, 1, now, now)
        self.trips[trip.id] = trip
        return trip

    async def get_trip(self, owner, trip_id):
        trip = self.trips.get(trip_id)
        return trip if trip and _owned_by(trip, owner) else None


def _replace_owner(trip: TripRecord, user_id) -> TripRecord:
    return replace(trip, user_id=user_id)


def _logger() -> TelemetryLogger:
    return TelemetryLogger(
        settings=TelemetrySettings(enabled=True, environment="test", service="twm-test", payload_mode=PayloadMode.OFF, max_field_size=4096),
        sink=InMemorySink(),
    )


def _auth_service(repository: MemoryUserRepository | None = None) -> AuthService:
    return AuthService(
        repository=repository or MemoryUserRepository(),
        settings=AuthSettings(jwt_secret="test-secret-at-least-32-bytes-long!!", jwt_cookie_secure=False, jwt_expiry_days=1),
        database_settings=DatabaseSettings(url=None, guest_cookie_secure=False),
        logger=_logger(),
    )


def _persistence(repository: MemoryUserRepository) -> TripPersistenceService:
    return TripPersistenceService(repository, DatabaseSettings(url=None, guest_cookie_secure=False))


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
    assert body["claimed_trip_count"] == 0
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


def test_signup_rejects_a_password_over_bcrypts_72_byte_limit(api_client: TestClient) -> None:
    """bcrypt raises rather than truncating past 72 bytes; this must be a
    clean 422 at the API boundary, not an unhandled 500."""
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "a" * 73})

    assert response.status_code == 422


def test_signup_rejects_a_password_that_exceeds_72_bytes_via_multibyte_characters(api_client: TestClient) -> None:
    """40 accented characters (2 bytes each in UTF-8) is under the 72-char
    limit but over the 72-byte limit bcrypt actually enforces."""
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "é" * 40})

    assert response.status_code == 422


def test_signup_accepts_a_password_at_exactly_72_bytes(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "a" * 72})

    assert response.status_code == 201


def test_login_succeeds_with_correct_credentials_and_sets_a_cookie(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})

    response = api_client.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})

    assert response.status_code == 200
    assert response.json()["email"] == "traveler@example.com"
    assert response.json()["claimed_trip_count"] == 0
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


def test_me_returns_the_current_user_for_a_valid_session(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})
    api_client.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})

    response = api_client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "traveler@example.com"


def test_me_rejects_a_request_with_no_session(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    assert api_client.get("/auth/me").status_code == 401


def test_me_rejects_an_invalid_cookie(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()
    api_client.cookies.set("twm_auth", "not-a-real-jwt")

    assert api_client.get("/auth/me").status_code == 401


def test_logout_clears_the_session_so_a_follow_up_me_call_is_unauthenticated(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})
    api_client.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})
    assert api_client.get("/auth/me").status_code == 200

    logout = api_client.post("/auth/logout")

    assert logout.status_code == 204
    assert api_client.get("/auth/me").status_code == 401


def test_logout_with_no_session_present_is_still_a_clean_204(api_client: TestClient) -> None:
    app.dependency_overrides[get_auth_service] = lambda: _auth_service()

    response = api_client.post("/auth/logout")

    assert response.status_code == 204


def test_guest_with_trips_who_signs_up_gets_them_claimed_and_visible_under_the_account(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    app.dependency_overrides[get_trip_persistence] = lambda: _persistence(repository)

    api_client.post("/trips", json={"title": "Rishikesh"})
    api_client.post("/trips", json={"title": "Goa"})

    signup = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})
    assert signup.status_code == 201
    assert signup.json()["claimed_trip_count"] == 2

    login = api_client.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})
    assert login.status_code == 200
    my_trips = api_client.get("/trips")
    assert len(my_trips.json()["trips"]) == 2


def test_guest_with_zero_trips_who_signs_up_claims_nothing(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    app.dependency_overrides[get_trip_persistence] = lambda: _persistence(repository)

    api_client.get("/trips")  # establishes a guest cookie with zero trips

    signup = api_client.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})

    assert signup.status_code == 201
    assert signup.json()["claimed_trip_count"] == 0


def test_a_returning_user_on_a_fresh_session_sees_only_their_claimed_trips(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    app.dependency_overrides[get_trip_persistence] = lambda: _persistence(repository)

    with TestClient(app) as first_browser:
        first_browser.post("/trips", json={"title": "Rishikesh"})
        first_browser.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})
        first_browser.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})

    with TestClient(app) as second_browser:
        # No guest cookie at all on this "device" — pure returning-user path.
        second_browser.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})
        trips = second_browser.get("/trips").json()["trips"]

    assert len(trips) == 1
    assert trips[0]["title"] == "Rishikesh"


def test_foreign_trip_access_returns_404_for_an_authenticated_user(api_client: TestClient) -> None:
    repository = MemoryUserRepository()
    app.dependency_overrides[get_auth_service] = lambda: _auth_service(repository)
    app.dependency_overrides[get_trip_persistence] = lambda: _persistence(repository)

    with TestClient(app) as stranger:
        foreign_trip_id = stranger.post("/trips", json={"title": "Not yours"}).json()["id"]

    with TestClient(app) as traveler:
        traveler.post("/auth/signup", json={"email": "traveler@example.com", "password": "hunter22!!"})
        traveler.post("/auth/login", json={"email": "traveler@example.com", "password": "hunter22!!"})
        response = traveler.get(f"/trips/{foreign_trip_id}")

    assert response.status_code == 404
