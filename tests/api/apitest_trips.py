"""API coverage for guest-owned database trip contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.dependencies import get_engine, get_logger, get_trip_persistence
from twm.main import app
from twm.persistence.contracts import GuestSession, TripCommandRecord, TripRecord, VersionConflictError
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings
from twm.prompt_registry import PromptRelease
from twm.schemas.trips import TripResponse
from twm.services import AgentExecution
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


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
        is_start = trip_state["guide_event"] == "START"
        return AgentExecution(
            response={
                "message": (
                    "Here are the places."
                    if is_start
                    else "Places approved; here is the day plan."
                ),
                "guide_state": {
                    "phase": "PLACES_DRAFT" if is_start else "DAY_PLAN_DRAFT",
                    "destinations": ["Rishikesh"],
                    "duration_days": 1,
                    "start_date": None,
                    "places": ["Triveni Ghat"],
                    "day_plan": (
                        []
                        if is_start
                        else [
                            {
                                "day_number": 1,
                                "date": None,
                                "places": ["Triveni Ghat"],
                            }
                        ]
                    ),
                    "preferences": [],
                    "exclusions": [],
                    "applied_changes": ["Approved places"],
                    "pending_clarification": None,
                },
                "explicit_changes": [],
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


class FakePlannerIntentEngine(FakeCommandEngine):
    async def scout(self, trip_state, message):
        self.calls.append(("scout", trip_state, message))
        return AgentExecution(
            response={
                "message": None,
                "state_delta": {
                    "trip_context": {
                        "destination": "Rishikesh",
                        "duration_days": 3,
                    }
                },
                "intent": "planner",
            },
            prompt_release=PromptRelease("scout", "1.0.0", "test"),
        )


class FakeGuideLifecycleEngine(FakeCommandEngine):
    def __init__(self, *, change_plan_on_approval=False):
        super().__init__()
        self.change_plan_on_approval = change_plan_on_approval

    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        guide_state = dict(trip_state["guide_state"])
        event = trip_state["guide_event"]
        if event == "APPROVE_PLACES":
            guide_state["phase"] = "DAY_PLAN_DRAFT"
            guide_state["day_plan"] = [
                {"day_number": 1, "date": None, "places": list(guide_state["places"])}
            ]
        elif event == "APPROVE_PLAN":
            guide_state["phase"] = "PLAN_APPROVED"
            if self.change_plan_on_approval:
                guide_state["places"] = ["Unexpected place"]
                guide_state["day_plan"] = [
                    {"day_number": 1, "date": None, "places": ["Unexpected place"]}
                ]
        return AgentExecution(
            response={
                "message": "Guide revision ready.",
                "guide_state": guide_state,
                "explicit_changes": [],
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeDayPlanClarificationEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        guide_state = dict(trip_state["guide_state"])
        if len(self.calls) == 1:
            guide_state["phase"] = "NEEDS_CLARIFICATION"
            guide_state["day_plan"] = []
            guide_state["preferences"] = ["adventure"]
            guide_state["pending_clarification"] = "Morning or evening?"
        else:
            guide_state["phase"] = "DAY_PLAN_DRAFT"
            guide_state["pending_clarification"] = None
        return AgentExecution(
            response={
                "message": "Clarification handled.",
                "guide_state": guide_state,
                "explicit_changes": ["preferences"] if len(self.calls) == 1 else [],
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakePlacesApprovalClarificationEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        guide_state = dict(trip_state["guide_state"])
        if trip_state["guide_event"] == "APPROVE_PLACES":
            guide_state["phase"] = "NEEDS_CLARIFICATION"
            guide_state["pending_clarification"] = "How many days?"
        else:
            guide_state["phase"] = "DAY_PLAN_DRAFT"
            guide_state["duration_days"] = 1
            guide_state["day_plan"] = [
                {
                    "day_number": 1,
                    "date": None,
                    "places": list(guide_state["places"]),
                }
            ]
            guide_state["pending_clarification"] = None
        return AgentExecution(
            response={
                "message": "Duration handled.",
                "guide_state": guide_state,
                "explicit_changes": (
                    []
                    if trip_state["guide_event"] == "APPROVE_PLACES"
                    else ["duration_days", "day_plan"]
                ),
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeDayPlanPlaceMutationEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        guide_state = dict(trip_state["guide_state"])
        guide_state["places"] = ["Unexpected place"]
        guide_state["day_plan"] = [
            {"day_number": 1, "date": None, "places": ["Unexpected place"]}
        ]
        return AgentExecution(
            response={
                "message": "Changed.",
                "guide_state": guide_state,
                "explicit_changes": [],
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeDayPlanDecisionLossEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        guide_state = dict(trip_state["guide_state"])
        guide_state["preferences"] = []
        guide_state["exclusions"] = []
        return AgentExecution(
            response={
                "message": "Moved.",
                "guide_state": guide_state,
                "explicit_changes": [],
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeExplicitPreferenceOverrideEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        guide_state = dict(trip_state["guide_state"])
        guide_state["exclusions"] = []
        return AgentExecution(
            response={
                "message": "Rafting is allowed again.",
                "guide_state": guide_state,
                "explicit_changes": ["exclusions"],
            },
            prompt_release=PromptRelease("guide", "1.1.0", "test"),
        )


def _service(repository):
    return TripPersistenceService(repository, DatabaseSettings(url=None, guest_cookie_secure=False))


def _create_seeded_trip(
    api_client: TestClient,
    repository: MemoryTripRepository,
    *,
    title: str = "Trip",
    trip_state=None,
    ui_state=None,
):
    created = api_client.post("/trips", json={"title": title}).json()
    trip_id = UUID(created["id"])
    record = repository.trips[trip_id]
    repository.trips[trip_id] = replace(
        record,
        trip_state=trip_state or {},
        ui_state=ui_state or {},
    )
    return created


def test_guest_trip_crud_without_delete_and_version_conflict(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    empty = api_client.get("/trips")
    assert empty.status_code == 200
    assert empty.json() == {"trips": []}
    cookie = empty.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Max-Age=15552000" in cookie

    created = api_client.post("/trips", json={"title": "Rishikesh"})
    assert created.status_code == 201
    trip = created.json()
    assert trip["version"] == 1

    trip_id = trip["id"]
    assert api_client.put(f"/trips/{trip_id}", json={}).status_code == 405

    stale = api_client.patch(f"/trips/{trip_id}", json={"expected_version": 2, "title": "New title"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 1

    renamed = api_client.patch(f"/trips/{trip_id}", json={"expected_version": 1, "title": "Spiritual Rishikesh"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Spiritual Rishikesh"
    assert api_client.delete(f"/trips/{trip_id}").status_code == 405


def test_guest_cannot_access_another_guests_trip():
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    with TestClient(app) as owner, TestClient(app) as stranger:
        trip_id = owner.post("/trips", json={"title": "Delhi"}).json()["id"]
        assert stranger.get(f"/trips/{trip_id}").status_code == 404
        assert stranger.patch(f"/trips/{trip_id}", json={"expected_version": 1, "title": "Mine"}).status_code == 404
    app.dependency_overrides.clear()


def test_trip_contract_rejects_invalid_version_and_mode(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    assert api_client.post("/trips", json={"title": "Trip", "product_mode": "concierge"}).status_code == 422
    assert api_client.post(
        "/trips", json={"title": "Trip", "trip_state": {}, "ui_state": {}}
    ).status_code == 422
    unknown_id = UUID(int=0)
    assert api_client.put(f"/trips/{unknown_id}", json={}).status_code == 405


def test_ui_state_update_preserves_canonical_trip_state(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    canonical = {"stage": "matching", "trip_context": {"budget": "50k"}}
    created = _create_seeded_trip(api_client, repository, trip_state=canonical)

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
        created = owner.post("/trips", json={"title": "Trip"}).json()
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
    trip = api_client.post("/trips", json={"title": "Rishikesh"}).json()
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
    trip = api_client.post("/trips", json={"title": "Trip"}).json()
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
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
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
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "approve_places", "expected_version": 1, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200
    saved = response.json()["trip"]["trip_state"]
    assert engine.calls[0][1]["guide_event"] == "APPROVE_PLACES"
    assert saved["planner_state"]["guide_session"]["revision"] == 3
    assert saved["planner_state"]["guide_session"]["state"]["phase"] == "DAY_PLAN_DRAFT"


def test_guide_approval_requires_the_backend_owned_current_phase(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideLifecycleEngine()
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True,
            environment="test",
            payload_mode=PayloadMode.METADATA,
            max_field_size=256,
        ),
        sink,
    )
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_logger] = lambda: logger
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": {
            "phase": "NEEDS_CLARIFICATION", "destinations": ["Rishikesh"],
            "duration_days": 1, "start_date": None, "places": [],
            "day_plan": [], "preferences": [], "exclusions": [],
            "applied_changes": [], "pending_clarification": "Which places?",
        }}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "approve_places", "expected_version": 1,
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422
    assert engine.calls == []
    assert api_client.get(f"/trips/{trip['id']}").json()["version"] == 1
    rejection = next(
        event
        for event in sink.events
        if event["event"] == "be.trip.command.invalid_transition"
    )
    assert rejection["level"] == "WARNING"
    assert rejection["message"] == "Rejected invalid Backend-owned trip command."
    assert rejection["fields"]["trip_id"] == trip["id"]
    assert rejection["fields"]["command"] == "approve_places"


def test_approve_plan_freezes_one_immutable_atlas_handoff(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "DAY_PLAN_DRAFT", "destinations": ["Rishikesh"],
        "duration_days": 1, "start_date": None, "places": ["Triveni Ghat"],
        "day_plan": [{"day_number": 1, "date": None, "places": ["Triveni Ghat"]}],
        "preferences": ["pilgrimage"], "exclusions": ["rafting"],
        "applied_changes": ["Removed rafting"], "pending_clarification": None,
    }
    state = {
        "stage": "planning", "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 4, "state": guide_state}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    payload = {"command": "approve_plan", "expected_version": 1,
               "idempotency_key": str(uuid4())}

    first = api_client.post(f"/trips/{trip['id']}/commands", json=payload)
    replay = api_client.post(f"/trips/{trip['id']}/commands", json=payload)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert len(engine.calls) == 1
    saved = first.json()["trip"]["trip_state"]
    assert saved["stage"] == "planned"
    assert saved["active_agent"] is None
    assert saved["planner_state"]["guide_session"]["revision"] == 5
    frozen = saved["planner_state"]["frozen_plan"]
    assert frozen["guide_revision"] == 5
    assert frozen["guide_state"] == saved["planner_state"]["guide_session"]["state"]

    rejected = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "traveler_message", "message": "Change the temple",
              "expected_version": 2, "idempotency_key": str(uuid4())},
    )
    assert rejected.status_code == 422
    assert len(engine.calls) == 1
    assert api_client.get(f"/trips/{trip['id']}").json()["version"] == 2


def test_approval_rejects_agent_changes_to_confirmed_plan(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideLifecycleEngine(change_plan_on_approval=True)
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "DAY_PLAN_DRAFT", "destinations": ["Rishikesh"],
        "duration_days": 1, "start_date": None, "places": ["Triveni Ghat"],
        "day_plan": [{"day_number": 1, "date": None, "places": ["Triveni Ghat"]}],
        "preferences": [], "exclusions": [], "applied_changes": [],
        "pending_clarification": None,
    }
    state = {
            "stage": "planning", "active_agent": "guide",
            "trip_context": {"destination": "Rishikesh"},
            "planner_state": {"guide_session": {"revision": 2, "state": guide_state}},
        }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "approve_plan", "expected_version": 1,
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422
    persisted = api_client.get(f"/trips/{trip['id']}").json()
    assert persisted["version"] == 1
    assert persisted["trip_state"]["planner_state"]["guide_session"]["state"] == guide_state


def test_day_plan_survives_a_backend_owned_clarification_round_trip(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakeDayPlanClarificationEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "DAY_PLAN_DRAFT",
        "destinations": ["Rishikesh"],
        "duration_days": 1,
        "start_date": None,
        "places": ["Triveni Ghat"],
        "day_plan": [
            {"day_number": 1, "date": None, "places": ["Triveni Ghat"]}
        ],
        "preferences": [],
        "exclusions": [],
        "applied_changes": [],
        "pending_clarification": None,
    }
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": guide_state}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    clarification = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Make it adventurous and move Triveni Ghat",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )
    assert clarification.status_code == 200
    pending = clarification.json()["trip"]["trip_state"]["planner_state"][
        "guide_session"
    ]
    assert pending["state"]["phase"] == "NEEDS_CLARIFICATION"
    assert pending["clarification_resume_phase"] == "DAY_PLAN_DRAFT"
    assert pending["clarification_base_state"]["day_plan"] == guide_state["day_plan"]
    assert pending["clarification_base_state"]["preferences"] == ["adventure"]

    resolved = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Evening",
            "expected_version": 2,
            "idempotency_key": str(uuid4()),
        },
    )
    assert resolved.status_code == 200
    assert engine.calls[1][1]["guide_state"]["day_plan"] == guide_state["day_plan"]
    assert engine.calls[1][1]["guide_state"]["preferences"] == ["adventure"]
    resumed = resolved.json()["trip"]["trip_state"]["planner_state"][
        "guide_session"
    ]
    assert resumed["revision"] == 3
    assert resumed["state"]["phase"] == "DAY_PLAN_DRAFT"
    assert resumed["state"]["day_plan"] == guide_state["day_plan"]
    assert resumed["state"]["preferences"] == ["adventure"]
    assert "clarification_base_state" not in resumed


def test_places_approval_can_collect_missing_duration_before_day_plan(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakePlacesApprovalClarificationEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "PLACES_DRAFT",
        "destinations": ["Rishikesh"],
        "duration_days": None,
        "start_date": None,
        "places": ["Triveni Ghat"],
        "day_plan": [],
        "preferences": [],
        "exclusions": [],
        "applied_changes": [],
        "pending_clarification": None,
    }
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": guide_state}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    pending_response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "approve_places",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )
    assert pending_response.status_code == 200
    pending = pending_response.json()["trip"]["trip_state"]["planner_state"][
        "guide_session"
    ]
    assert pending["state"]["phase"] == "NEEDS_CLARIFICATION"
    assert pending["clarification_resume_phase"] == "DAY_PLAN_DRAFT"

    resolved_response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "One day",
            "expected_version": 2,
            "idempotency_key": str(uuid4()),
        },
    )
    assert resolved_response.status_code == 200
    resolved = resolved_response.json()["trip"]["trip_state"]["planner_state"][
        "guide_session"
    ]
    assert resolved["state"]["phase"] == "DAY_PLAN_DRAFT"
    assert resolved["state"]["duration_days"] == 1
    assert resolved["state"]["places"] == ["Triveni Ghat"]


def test_day_plan_edits_cannot_reopen_the_approved_places_boundary(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakeDayPlanPlaceMutationEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "DAY_PLAN_DRAFT",
        "destinations": ["Rishikesh"],
        "duration_days": 1,
        "start_date": None,
        "places": ["Triveni Ghat"],
        "day_plan": [
            {"day_number": 1, "date": None, "places": ["Triveni Ghat"]}
        ],
        "preferences": [],
        "exclusions": [],
        "applied_changes": [],
        "pending_clarification": None,
    }
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": guide_state}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Move Triveni Ghat to the evening",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422
    persisted = api_client.get(f"/trips/{trip['id']}").json()
    assert persisted["version"] == 1
    assert persisted["trip_state"]["planner_state"]["guide_session"]["state"] == guide_state


def test_day_plan_edits_cannot_drop_confirmed_preferences_or_exclusions(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakeDayPlanDecisionLossEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "DAY_PLAN_DRAFT",
        "destinations": ["Rishikesh"],
        "duration_days": 1,
        "start_date": None,
        "places": ["Triveni Ghat"],
        "day_plan": [
            {"day_number": 1, "date": None, "places": ["Triveni Ghat"]}
        ],
        "preferences": ["pilgrimage"],
        "exclusions": ["rafting"],
        "applied_changes": ["Removed rafting"],
        "pending_clarification": None,
    }
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": guide_state}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Move Triveni Ghat to the evening",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert "preferences" in response.json()["detail"]
    assert "exclusions" in response.json()["detail"]
    persisted = api_client.get(f"/trips/{trip['id']}").json()
    assert persisted["version"] == 1
    assert persisted["trip_state"]["planner_state"]["guide_session"]["state"] == guide_state


def test_day_plan_accepts_an_explicit_traveler_preference_override(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakeExplicitPreferenceOverrideEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    guide_state = {
        "phase": "DAY_PLAN_DRAFT",
        "destinations": ["Rishikesh"],
        "duration_days": 1,
        "start_date": None,
        "places": ["Triveni Ghat"],
        "day_plan": [
            {"day_number": 1, "date": None, "places": ["Triveni Ghat"]}
        ],
        "preferences": ["pilgrimage"],
        "exclusions": ["rafting"],
        "applied_changes": ["Removed rafting"],
        "pending_clarification": None,
    }
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": guide_state}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Actually rafting is fine.",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    session = response.json()["trip"]["trip_state"]["planner_state"][
        "guide_session"
    ]
    assert session["revision"] == 2
    assert session["state"]["preferences"] == ["pilgrimage"]
    assert session["state"]["exclusions"] == []
    assert session["explicit_changes"] == ["exclusions"]


def test_scout_matcher_intent_hands_off_to_meridian_in_same_command(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Mountains"}).json()
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


def test_start_planning_invokes_guide_from_backend_owned_destination(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "matched",
        "active_agent": None,
        "advisor_state": None,
        "matcher_state": None,
        "planner_state": None,
        "trip_context": {
            "selected_option": {
                "type": "single", "id": "rishikesh", "name": "Rishikesh"
            }
        },
    }
    trip = _create_seeded_trip(
        api_client, repository, title="Rishikesh", trip_state=state
    )
    payload = {
        "command": "start_planning",
        "expected_version": 1,
        "idempotency_key": str(uuid4()),
    }

    first = api_client.post(f"/trips/{trip['id']}/commands", json=payload)
    replay = api_client.post(f"/trips/{trip['id']}/commands", json=payload)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert [call[0] for call in engine.calls] == ["guide"]
    assert engine.calls[0][1]["guide_event"] == "START"
    saved = first.json()["trip"]["trip_state"]
    assert saved["stage"] == "planning"
    assert saved["active_agent"] == "guide"
    assert saved["advisor_state"] == {"conversation_context": {}, "artifacts": []}
    assert saved["matcher_state"] == {"conversation_context": {}, "recommendations": []}
    assert saved["planner_state"]["guide_session"]["revision"] == 1


def test_scout_planner_intent_starts_guide_from_owned_context(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakePlannerIntentEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Rishikesh"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Plan Rishikesh for three days",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["scout", "guide"]
    assert engine.calls[1][1]["guide_event"] == "START"
    assert engine.calls[1][1]["trip_context"]["destination"] == "Rishikesh"
    saved = response.json()["trip"]["trip_state"]
    assert saved["stage"] == "planning"
    assert saved["planner_state"]["guide_session"]["state"]["phase"] == "PLACES_DRAFT"


def test_start_planning_requires_backend_owned_destination(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "start_planning",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert engine.calls == []
    assert api_client.get(f"/trips/{trip['id']}").json()["version"] == 1


def test_continue_resumes_backend_selected_agent_without_message(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()
    payload = {
        "command": "continue",
        "expected_version": 1,
        "idempotency_key": str(uuid4()),
    }

    first = api_client.post(f"/trips/{trip['id']}/commands", json=payload)
    replay = api_client.post(f"/trips/{trip['id']}/commands", json=payload)

    assert first.status_code == 200
    assert replay.json() == first.json()
    assert len(engine.calls) == 1
    assert engine.calls[0][0] == "scout"
    assert engine.calls[0][2] is None


def test_matched_scout_handoff_clears_obsolete_selection(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "matched",
        "active_agent": None,
        "trip_context": {
            "selected_option": {
                "type": "single", "id": "goa", "name": "Goa"
            }
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Actually suggest mountains instead",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["scout", "meridian"]
    assert "selected_option" not in engine.calls[1][1]["trip_context"]
    assert "selected_option" not in response.json()["trip"]["trip_state"]["trip_context"]


def test_advice_entry_invokes_scout_only(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "advice_entry",
            "message": "Where should I go for a 2 week end-of-year trip?",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["scout"]
    saved = response.json()["trip"]
    assert saved["version"] == 2
    assert saved["trip_state"]["trip_context"]["destination"] == "Rishikesh"


def test_advice_entry_hands_off_to_meridian_in_same_command(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Mountains"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "advice_entry",
            "message": "Suggest mountains",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["scout", "meridian"]
    saved = response.json()["trip"]
    assert saved["version"] == 2
    assert saved["trip_state"]["stage"] == "recommended"


def test_advice_entry_requires_message(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: FakeCommandEngine()
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "advice_entry", "expected_version": 1, "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422


def test_discover_entry_invokes_meridian_with_no_scout_call(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "discover_entry",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["meridian"]
    saved = response.json()["trip"]
    assert saved["version"] == 2
    assert saved["trip_state"]["stage"] in {"matching", "recommended"}


def test_known_destination_entry_invokes_guide_with_no_scout_or_meridian_call(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "known_destination_entry",
            "destination": "Goa",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["guide"]
    saved = response.json()["trip"]
    assert saved["version"] == 2
    assert saved["trip_state"]["trip_context"]["destination"] == "Goa"
    assert saved["trip_state"]["stage"] == "planning"
    assert saved["trip_state"]["active_agent"] == "guide"


def test_known_destination_entry_missing_destination_returns_deterministic_clarification(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "known_destination_entry",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert engine.calls == []
    assert response.json()["message"] == "Tell us the destination before starting the plan."
    saved = response.json()["trip"]
    assert saved["version"] == 2
    assert saved["trip_state"]["stage"] == "new"
