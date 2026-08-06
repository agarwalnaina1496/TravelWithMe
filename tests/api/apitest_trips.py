"""API coverage for guest-owned database trip contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.core import get_trip_persistence
from twm.main import app
from twm.persistence.contracts import GuestSession, TripRecord, VersionConflictError
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings


class MemoryTripRepository:
    def __init__(self):
        self.guests = {}
        self.trips = {}

    async def resolve_guest(self, token_hash, lifetime_days):
        guest = self.guests.get(token_hash)
        if guest and guest.expires_at > datetime.now(timezone.utc):
            renewed = replace(guest, expires_at=datetime.now(timezone.utc) + timedelta(days=lifetime_days))
            self.guests[token_hash] = renewed
            return renewed
        return None

    async def create_guest(self, token_hash, lifetime_days):
        guest = GuestSession(uuid4(), datetime.now(timezone.utc) + timedelta(days=lifetime_days))
        self.guests[token_hash] = guest
        return guest

    async def list_trips(self, guest_id):
        return [trip for trip in self.trips.values() if trip.guest_session_id == guest_id]

    async def create_trip(self, guest_id, title, product_mode, trip_state, ui_state):
        now = datetime.now(timezone.utc)
        trip = TripRecord(uuid4(), guest_id, title, product_mode, trip_state, ui_state, 1, now, now)
        self.trips[trip.id] = trip
        return trip

    async def get_trip(self, guest_id, trip_id):
        trip = self.trips.get(trip_id)
        return trip if trip and trip.guest_session_id == guest_id else None

    async def replace_trip(self, guest_id, trip_id, expected_version, trip_state, ui_state):
        trip = await self.get_trip(guest_id, trip_id)
        if not trip:
            return None
        if trip.version != expected_version:
            raise VersionConflictError(trip.version)
        updated = replace(trip, trip_state=trip_state, ui_state=ui_state, version=trip.version + 1, updated_at=datetime.now(timezone.utc))
        self.trips[trip_id] = updated
        return updated

    async def rename_trip(self, guest_id, trip_id, expected_version, title):
        trip = await self.get_trip(guest_id, trip_id)
        if not trip:
            return None
        if trip.version != expected_version:
            raise VersionConflictError(trip.version)
        updated = replace(trip, title=title, version=trip.version + 1, updated_at=datetime.now(timezone.utc))
        self.trips[trip_id] = updated
        return updated


def _service(repository):
    return TripPersistenceService(repository, DatabaseSettings(url=None, guest_cookie_secure=False))


def test_guest_trip_crud_without_delete_and_version_conflict(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    empty = api_client.get("/trips")
    assert empty.status_code == 200
    assert empty.json() == {"trips": []}
    cookie = empty.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Max-Age=15552000" in cookie

    created = api_client.post("/trips", json={"title": "Rishikesh", "trip_state": {"stage": "planning"}, "ui_state": {"panel": "guide"}})
    assert created.status_code == 201
    trip = created.json()
    assert trip["version"] == 1

    trip_id = trip["id"]
    replaced = api_client.put(f"/trips/{trip_id}", json={"expected_version": 1, "trip_state": {"stage": "places_approved"}, "ui_state": {"panel": "guide"}})
    assert replaced.status_code == 200
    assert replaced.json()["version"] == 2

    stale = api_client.patch(f"/trips/{trip_id}", json={"expected_version": 1, "title": "New title"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 2

    renamed = api_client.patch(f"/trips/{trip_id}", json={"expected_version": 2, "title": "Spiritual Rishikesh"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Spiritual Rishikesh"
    assert api_client.delete(f"/trips/{trip_id}").status_code == 405


def test_guest_cannot_access_another_guests_trip():
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    with TestClient(app) as owner, TestClient(app) as stranger:
        trip_id = owner.post("/trips", json={"title": "Delhi", "trip_state": {}, "ui_state": {}}).json()["id"]
        assert stranger.get(f"/trips/{trip_id}").status_code == 404
        assert stranger.patch(f"/trips/{trip_id}", json={"expected_version": 1, "title": "Mine"}).status_code == 404
    app.dependency_overrides.clear()


def test_trip_contract_rejects_invalid_version_and_mode(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    assert api_client.post("/trips", json={"title": "Trip", "product_mode": "concierge", "trip_state": {}, "ui_state": {}}).status_code == 422
    unknown_id = UUID(int=0)
    assert api_client.put(f"/trips/{unknown_id}", json={"expected_version": 0, "trip_state": {}, "ui_state": {}}).status_code == 422
