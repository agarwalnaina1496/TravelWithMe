"""API coverage for guest-owned database trip contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.core import get_engine, get_trip_persistence
from twm.main import app
from twm.persistence.contracts import GuestSession, TripCommandRecord, TripRecord, VersionConflictError
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings
from twm.prompts import PromptRelease
from twm.schemas.trips import TripResponse
from twm.services import AgentExecution


class MemoryTripRepository:
    def __init__(self):
        self.guests = {}
        self.trips = {}
        self.commands = {}

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

    async def update_ui_state(self, guest_id, trip_id, expected_version, ui_state):
        trip = await self.get_trip(guest_id, trip_id)
        if not trip:
            return None
        if trip.version != expected_version:
            raise VersionConflictError(trip.version)
        updated = replace(trip, ui_state=ui_state, version=trip.version + 1, updated_at=datetime.now(timezone.utc))
        self.trips[trip_id] = updated
        return updated

    async def get_command(self, guest_id, trip_id, idempotency_key):
        return self.commands.get((guest_id, trip_id, idempotency_key))

    async def commit_command(self, guest_id, trip_id, expected_version, idempotency_key, request_hash, trip_state, response):
        key = (guest_id, trip_id, idempotency_key)
        if key in self.commands:
            return self.commands[key]
        trip = await self.get_trip(guest_id, trip_id)
        if not trip:
            return None
        if trip.version != expected_version:
            raise VersionConflictError(trip.version)
        updated = replace(trip, trip_state=trip_state, version=trip.version + 1, updated_at=datetime.now(timezone.utc))
        self.trips[trip_id] = updated
        stored = dict(response)
        stored["trip"] = TripResponse.model_validate(updated, from_attributes=True).model_dump(mode="json")
        record = TripCommandRecord(request_hash, stored)
        self.commands[key] = record
        return updated


class FakeCommandEngine:
    def __init__(self):
        self.calls = []

    async def scout(self, trip_state, message):
        self.calls.append(("scout", trip_state, message))
        return AgentExecution(
            response={
                "message": "Rishikesh context saved.",
                "state_delta": {"trip_context": {"destination": "Rishikesh"}},
                "intent": None,
            },
            prompt_release=PromptRelease("scout", "1.0.0", "test"),
        )

    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        return AgentExecution(
            response={
                "message": "Places approved; here is the day plan.",
                "guide_state": {
                    "phase": "DAY_PLAN_DRAFT",
                    "destinations": ["Rishikesh"],
                    "duration_days": 1,
                    "start_date": None,
                    "places": ["Triveni Ghat"],
                    "day_plan": [{"day_number": 1, "date": None, "places": ["Triveni Ghat"]}],
                    "preferences": [],
                    "exclusions": [],
                    "applied_changes": ["Approved places"],
                    "pending_clarification": None,
                },
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeHandoffEngine(FakeCommandEngine):
    async def scout(self, trip_state, message):
        self.calls.append(("scout", trip_state, message))
        return AgentExecution(
            response={
                "message": None,
                "state_delta": {"trip_context": {"destination_scope": "mountains"}},
                "intent": "matcher",
            },
            prompt_release=PromptRelease("scout", "1.0.0", "test"),
        )

    async def meridian(self, trip_state, message):
        self.calls.append(("meridian", trip_state, message))
        return AgentExecution(
            response={
                "status": "HARD_FAIL",
                "message": "I need a little more flexibility before I can recommend.",
                "state_delta": {"trip_context": {}, "matcher_state": {
                    "conversation_context": {"last_meridian_message": "I need a little more flexibility before I can recommend.", "awaiting": None}
                }},
                "options": [],
            },
            prompt_release=PromptRelease("meridian", "1.0.0", "test"),
        )


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


def test_ui_state_update_preserves_canonical_trip_state(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    canonical = {"stage": "matching", "trip_context": {"budget": "50k"}}
    created = api_client.post(
        "/trips", json={"title": "Trip", "trip_state": canonical, "ui_state": {}}
    ).json()

    updated = api_client.patch(
        f"/trips/{created['id']}/ui-state",
        json={"expected_version": 1, "ui_state": {"last_screen": "chat"}},
    )

    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["trip_state"] == canonical
    assert updated.json()["ui_state"] == {"last_screen": "chat"}
    assert api_client.patch(
        f"/trips/{created['id']}/ui-state",
        json={"expected_version": 1, "ui_state": {"last_screen": "recos"}},
    ).status_code == 409


def test_ui_state_contract_rejects_trip_state_and_foreign_owner():
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    with TestClient(app) as owner, TestClient(app) as stranger:
        created = owner.post(
            "/trips", json={"title": "Trip", "trip_state": {"stage": "new"}, "ui_state": {}}
        ).json()
        rejected = owner.patch(
            f"/trips/{created['id']}/ui-state",
            json={"expected_version": 1, "ui_state": {}, "trip_state": {}},
        )
        assert rejected.status_code == 422
        hidden = stranger.patch(
            f"/trips/{created['id']}/ui-state",
            json={"expected_version": 1, "ui_state": {"last_screen": "chat"}},
        )
        assert hidden.status_code == 404
    app.dependency_overrides.clear()


def test_trip_command_loads_owned_state_and_replays_idempotently(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post(
        "/trips", json={"title": "Rishikesh", "trip_state": {}, "ui_state": {}}
    ).json()
    payload = {
        "command": "traveler_message",
        "message": "I want to visit Rishikesh",
        "expected_version": 1,
        "idempotency_key": str(uuid4()),
    }

    first = api_client.post(f"/trips/{trip['id']}/commands", json=payload)
    replay = api_client.post(f"/trips/{trip['id']}/commands", json=payload)

    assert first.status_code == 200
    assert first.json()["trip"]["version"] == 2
    assert first.json()["trip"]["trip_state"]["trip_context"] == {"destination": "Rishikesh"}
    assert replay.json() == first.json()
    assert len(engine.calls) == 1
    assert "matcher_state" not in engine.calls[0][1]


def test_trip_command_rejects_browser_state_and_reused_key(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post(
        "/trips", json={"title": "Trip", "trip_state": {}, "ui_state": {}}
    ).json()
    key = str(uuid4())
    base = {"command": "traveler_message", "message": "hello", "expected_version": 1, "idempotency_key": key}
    assert api_client.post(f"/trips/{trip['id']}/commands", json={**base, "trip_state": {}}).status_code == 422
    assert api_client.post(f"/trips/{trip['id']}/commands", json=base).status_code == 200
    changed = {**base, "message": "different"}
    assert api_client.post(f"/trips/{trip['id']}/commands", json=changed).status_code == 409


def test_deterministic_selection_uses_latest_backend_recommendation(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: FakeCommandEngine()
    state = {
        "stage": "recommended",
        "active_agent": None,
        "trip_context": {},
        "matcher_state": {"recommendations": [{"options": [
            {"rank": 1, "type": "single", "destination_id": "rishikesh", "name": "Rishikesh"}
        ]}]},
    }
    trip = api_client.post(
        "/trips", json={"title": "Trip", "trip_state": state, "ui_state": {}}
    ).json()
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "select_destination",
            "option_id": "rishikesh",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 200
    saved = response.json()["trip"]["trip_state"]
    assert saved["stage"] == "matched"
    assert saved["trip_context"]["selected_option"] == {
        "type": "single", "id": "rishikesh", "name": "Rishikesh"
    }


def test_approve_places_invokes_guide_from_persisted_session(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 2, "state": {
            "phase": "PLACES_DRAFT", "destinations": ["Rishikesh"],
            "duration_days": 1, "start_date": None, "places": ["Triveni Ghat"],
            "day_plan": [], "preferences": [], "exclusions": [],
            "applied_changes": [], "pending_clarification": None,
        }}},
    }
    trip = api_client.post(
        "/trips", json={"title": "Trip", "trip_state": state, "ui_state": {}}
    ).json()
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "approve_places", "expected_version": 1, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200
    saved = response.json()["trip"]["trip_state"]
    assert engine.calls[0][1]["guide_event"] == "APPROVE_PLACES"
    assert saved["planner_state"]["guide_session"]["revision"] == 3
    assert saved["planner_state"]["guide_session"]["state"]["phase"] == "DAY_PLAN_DRAFT"


def test_scout_matcher_intent_hands_off_to_meridian_in_same_command(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post(
        "/trips", json={"title": "Mountains", "trip_state": {}, "ui_state": {}}
    ).json()
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message", "message": "Suggest mountains",
            "expected_version": 1, "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["scout", "meridian"]
    assert engine.calls[1][1]["trip_context"]["destination_scope"] == "mountains"
    saved = response.json()["trip"]
    assert saved["version"] == 2
    assert saved["trip_state"]["stage"] == "recommended"
