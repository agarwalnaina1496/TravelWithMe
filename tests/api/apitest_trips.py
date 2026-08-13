"""API coverage for guest-owned database trip contracts."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.dependencies import get_engine, get_logger, get_trip_persistence
from twm.main import app
from twm.persistence.contracts import GuestSession, ItineraryVersionRecord, RecommendationRecord, TripCommandRecord, TripRecord, VersionConflictError
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
        self.recommendations = {}  # trip_id -> list[RecommendationRecord], latest last
        self.itinerary_versions = {}  # trip_id -> list[ItineraryVersionRecord], ordered

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

    async def get_latest_recommendation(self, guest_id, trip_id):
        trip = await self.get_trip(guest_id, trip_id)
        if not trip:
            return None
        rounds = self.recommendations.get(trip_id) or []
        return rounds[-1] if rounds else None

    async def list_itinerary_versions(self, guest_id, trip_id):
        trip = await self.get_trip(guest_id, trip_id)
        if not trip:
            return []
        return list(self.itinerary_versions.get(trip_id) or [])

    async def commit_command(self, guest_id, trip_id, expected_version, idempotency_key, request_hash, trip_state, response_trip_state, response, new_recommendation=None, new_itinerary_version=None):
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
        if new_recommendation is not None:
            self.recommendations.setdefault(trip_id, []).append(RecommendationRecord(
                trip_id=trip_id,
                version=new_recommendation["version"],
                status=new_recommendation["status"],
                message=new_recommendation["message"],
                trip_type=new_recommendation.get("trip_type"),
                options=new_recommendation.get("options") or [],
                traveler_criteria=new_recommendation.get("traveler_criteria"),
                constraint_adjustment_suggestions=new_recommendation.get("constraint_adjustment_suggestions"),
                agent_meta=new_recommendation["agent_meta"],
                created_at=datetime.now(timezone.utc),
            ))
        if new_itinerary_version is not None:
            self.itinerary_versions.setdefault(trip_id, []).append(ItineraryVersionRecord(
                trip_id=trip_id,
                version=new_itinerary_version["version"],
                source_guide_revision=new_itinerary_version["source_guide_revision"],
                result=new_itinerary_version["result"],
                created_at=datetime.now(timezone.utc),
            ))
        stored = dict(response)
        response_record = replace(updated, trip_state=response_trip_state)
        stored["trip"] = TripResponse.model_validate(response_record, from_attributes=True).model_dump(mode="json")
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
                "state_delta": {
                    "trip_context": {
                        "destinations": ["Rishikesh"],
                        "duration_days": 1,
                    },
                    "planner_state": (
                        {
                            "conversation_context": {"awaiting": None},
                            "places": ["Triveni Ghat"],
                        }
                        if is_start
                        else {
                            "day_plan": [
                                {
                                    "day_number": 1,
                                    "date": None,
                                    "places": ["Triveni Ghat"],
                                    "pace": "balanced",
                                    "buffer_note": None,
                                }
                            ]
                        }
                    ),
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
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        event = trip_state["guide_event"]
        planner_state = dict(trip_state["planner_state"])
        # APPROVE_PLAN never reaches the engine — Backend applies it
        # deterministically — so there is no branch for it here.
        planner_delta = {}
        if event == "APPROVE_PLACES":
            planner_delta["day_plan"] = [
                {
                    "day_number": 1,
                    "date": None,
                    "places": list(planner_state["places"]),
                    "pace": "balanced",
                    "buffer_note": None,
                }
            ]
        return AgentExecution(
            response={
                "message": "Guide revision ready.",
                "state_delta": {"planner_state": planner_delta},
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeAtlasLifecycleEngine(FakeGuideLifecycleEngine):
    async def atlas(self, trip_state, message):
        self.calls.append(("atlas", trip_state, message))
        working_plan = trip_state["working_plan"]
        confirmed_anchors = trip_state.get("confirmed_anchors") or []
        anchor_by_day = {
            anchor["day_number"]: anchor
            for anchor in confirmed_anchors
            if anchor.get("day_number")
        }
        return AgentExecution(
            response={
                "final_itinerary": {
                    "trip_summary": {
                        "title": "Trip",
                        "destinations": working_plan["destinations"],
                        "duration_days": working_plan["duration_days"],
                        "overview": "Overview.",
                        "route_rationale": "Rationale.",
                    },
                    "travel_options": [],
                    "stay_options": [],
                    "days": [
                        {
                            "day_number": day["day_number"],
                            "title": (
                                f"{anchor_by_day[day['day_number']]['label']} (confirmed)"
                                if day["day_number"] in anchor_by_day
                                else f"Day {day['day_number']}"
                            ),
                            "primary_location": working_plan["destinations"][0],
                            "summary": "Summary.",
                            "timeline": [
                                {
                                    "kind": "ACTIVITY",
                                    "title": "Explore",
                                    "location": working_plan["destinations"][0],
                                    "detail": "Detail.",
                                    "reference": {"status": "GENERAL_GUIDANCE"},
                                }
                            ],
                            "seasonal_guidance": "Guidance.",
                            "permit_or_ticket_guidance": "Guidance.",
                        }
                        for day in working_plan["days"]
                    ],
                    "budget_summary": {
                        "currency": "INR",
                        "lines": [
                            {
                                "category": "Stay",
                                "amount_low": 1000,
                                "amount_high": 2000,
                                "note": "Estimated.",
                            }
                        ],
                        "budget_fit": "Fits within a typical budget.",
                    },
                    "practical_notes": [],
                    "sources": [],
                    "assumptions": (
                        [
                            {
                                "category": "dates",
                                "detail": "Assumed a start date since none was confirmed.",
                            }
                        ]
                        if not working_plan.get("start_date")
                        else []
                    ),
                },
                "unresolved": [],
            },
            prompt_release=PromptRelease("atlas", "1.0.0", "test"),
        )


class FakeDayPlanClarificationEngine(FakeCommandEngine):
    """First turn asks an ordinary (non-duration) ambiguity clarification —
    no state_delta at all, since `awaiting` is reserved for the missing-
    duration gate and everything else is left untouched until the
    traveler's next message resolves it. Second turn applies the answer."""

    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        if len(self.calls) == 1:
            return AgentExecution(
                response={
                    "message": "Morning or evening for the temple visit?",
                    "state_delta": {},
                },
                prompt_release=PromptRelease("guide", "1.0.0", "test"),
            )
        return AgentExecution(
            response={
                "message": "Got it — noted for the evening.",
                "state_delta": {
                    "trip_context": {"preferences": ["adventure"]},
                },
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


class FakeGuideReversalEngine(FakeCommandEngine):
    """Guide proposes reopen_destination_discovery; Backend validates and hands off to Meridian."""

    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        return AgentExecution(
            response={
                "message": "Let's look at other destinations.",
                "state_delta": {},
                "outcome": "reopen_destination_discovery",
            },
            prompt_release=PromptRelease("guide", "1.2.0", "test"),
        )

    async def meridian(self, trip_state, message):
        self.calls.append(("meridian", trip_state, message))
        return AgentExecution(
            response={
                "status": "NEEDS_CLARIFICATION",
                "message": "What matters most for the next destination?",
                "state_delta": {"trip_context": {}, "matcher_state": {
                    "conversation_context": {"last_meridian_message": "What matters most for the next destination?", "awaiting": "preferences"}
                }},
                "options": [],
            },
            prompt_release=PromptRelease("meridian", "1.0.0", "test"),
        )


class FakeGuideOrdinaryEditEngine(FakeCommandEngine):
    """Guide handles a normal edit and stays with outcome = continue (the default)."""

    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        places = [*trip_state["planner_state"]["places"], "Anjuna Beach"]
        return AgentExecution(
            response={
                "message": "Added Anjuna Beach.",
                "state_delta": {"planner_state": {"places": places}},
            },
            prompt_release=PromptRelease("guide", "1.2.0", "test"),
        )


def _seeded_guide_places_state(destination="Goa", places=("Baga Beach",)):
    return {
        "stage": "planning",
        "active_agent": "guide",
        "advisor_state": None,
        "matcher_state": None,
        "trip_context": {"destinations": [destination]},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": list(places),
            "day_plan": [],
            "revision": 1,
        },
    }


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


def _seed_recommendation(
    repository: MemoryTripRepository,
    trip_id: UUID,
    *,
    version: int = 1,
    status: str = "SUCCESS",
    message: str = "Here are your matches.",
    trip_type: str | None = "single",
    options=(),
    traveler_criteria=None,
    agent_meta=None,
):
    """Seeds a matcher_recommendations row directly (TWM-153) — recommendations
    are no longer part of trip_state, so tests that need a prior round for
    select_destination/more_like_this seed the repository's own table."""
    repository.recommendations.setdefault(trip_id, []).append(RecommendationRecord(
        trip_id=trip_id, version=version, status=status, message=message, trip_type=trip_type,
        options=list(options), traveler_criteria=traveler_criteria, constraint_adjustment_suggestions=None,
        agent_meta=agent_meta or {"agent": "meridian", "prompt_version": "1.0.0"},
        created_at=datetime.now(timezone.utc),
    ))


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


def test_list_trips_includes_trip_state_and_ui_state_so_the_ui_can_render_cards_without_n_extra_fetches(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    state = {"stage": "matching", "trip_context": {"origin": "Delhi", "budget": "₹1,00,000"}}
    ui_state = {"destinationsOpenId": "gwalior-orchha-khajuraho-panna"}
    _create_seeded_trip(api_client, repository, title="Rishikesh", trip_state=state, ui_state=ui_state)

    listed = api_client.get("/trips")
    assert listed.status_code == 200
    [summary] = listed.json()["trips"]
    assert summary["trip_state"] == state
    assert summary["ui_state"] == ui_state


def test_guest_cannot_access_another_guests_trip():
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    with TestClient(app) as owner, TestClient(app) as stranger:
        trip_id = owner.post("/trips", json={"title": "Delhi"}).json()["id"]
        assert stranger.get(f"/trips/{trip_id}").status_code == 404
        assert stranger.patch(f"/trips/{trip_id}", json={"expected_version": 1, "title": "Mine"}).status_code == 404
        assert stranger.get("/trips").json() == {"trips": []}
        assert len(owner.get("/trips").json()["trips"]) == 1
    app.dependency_overrides.clear()


def test_list_get_and_rename_log_structured_success_and_not_found_events(api_client: TestClient):
    repository = MemoryTripRepository()
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
    app.dependency_overrides[get_logger] = lambda: logger

    api_client.get("/trips")
    listed = next(event for event in sink.events if event["event"] == "be.trip.listed")
    assert listed["level"] == "INFO"
    assert listed["fields"]["count"] == 0

    trip_id = api_client.post("/trips", json={"title": "Delhi"}).json()["id"]

    api_client.get(f"/trips/{trip_id}")
    fetched = next(event for event in sink.events if event["event"] == "be.trip.fetched")
    assert fetched["level"] == "INFO"
    assert fetched["fields"]["trip_id"] == trip_id

    api_client.patch(f"/trips/{trip_id}", json={"expected_version": 1, "title": "New Delhi"})
    renamed = next(event for event in sink.events if event["event"] == "be.trip.renamed")
    assert renamed["level"] == "INFO"
    assert renamed["fields"]["trip_id"] == trip_id
    assert renamed["fields"]["version"] == 2

    unknown_id = uuid4()
    api_client.get(f"/trips/{unknown_id}")
    not_found = next(event for event in sink.events if event["event"] == "be.trip.not_found")
    assert not_found["level"] == "WARNING"
    assert not_found["fields"]["trip_id"] == str(unknown_id)


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


def test_command_response_omits_untouched_branches_and_advisor_state(api_client: TestClient):
    """A scout-only turn must not carry planner/itinerary/logistics/matcher
    state it never touched, or advisor_state (never read by anyone, TWM-154).
    Core fields (stage/active_agent/trip_context) and the touched branch
    (advisor_state.conversation_context lives only server-side; nothing
    scout-only touches is included) still round-trip correctly.
    """
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Rishikesh"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "I want to visit Rishikesh",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    state = response.json()["trip"]["trip_state"]
    assert state["stage"] == "new"
    assert state["active_agent"] == "scout"
    assert state["trip_context"] == {"destination": "Rishikesh"}
    assert "advisor_state" not in state
    assert "matcher_state" not in state
    assert "planner_state" not in state
    assert "itinerary_state" not in state
    assert "logistics_state" not in state
    # But the full state is still fully persisted and resumable via GET —
    # advisor_state.conversation_context is written server-side (Scout reads
    # it back), just never returned in a command response.
    persisted = api_client.get(f"/trips/{trip['id']}").json()["trip_state"]
    assert persisted["advisor_state"]["conversation_context"]["last_advisor_message"] == "Rishikesh context saved."
    assert "artifacts" not in persisted["advisor_state"]


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
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    _seed_recommendation(repository, UUID(trip["id"]), options=[
        {"rank": 1, "type": "single", "destination_id": "rishikesh", "name": "Rishikesh"}
    ])
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
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": 1},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": [],
            "revision": 2,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "approve_places", "expected_version": 1, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200
    saved = response.json()["trip"]["trip_state"]
    assert engine.calls[0][1]["guide_event"] == "APPROVE_PLACES"
    assert saved["planner_state"]["revision"] == 3
    day_plan = saved["planner_state"]["day_plan"]
    assert day_plan
    assert all(day["pace"] in {"relaxed", "balanced", "packed"} for day in day_plan)


class FakeTradeoffExplainingEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        return AgentExecution(
            response={
                "message": "Removed Ram Jhula from Day 1 — that opens up a free afternoon instead of a packed day.",
                "state_delta": {
                    "planner_state": {
                        "places": ["Triveni Ghat"],
                        "day_plan": [
                            {"day_number": 1, "date": None, "places": ["Triveni Ghat"], "pace": "relaxed", "buffer_note": "Free afternoon after removing Ram Jhula."},
                        ],
                    },
                },
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


def test_traveler_message_response_explains_a_meaningful_tradeoff(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeTradeoffExplainingEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "planning", "active_agent": "guide",
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": 1},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat", "Ram Jhula"],
            "day_plan": [{"day_number": 1, "date": None, "places": ["Triveni Ghat", "Ram Jhula"], "pace": "packed", "buffer_note": None}],
            "revision": 2,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "traveler_message", "message": "Remove Ram Jhula from day 1.",
              "expected_version": 1, "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Removed Ram Jhula from Day 1 — that opens up a free afternoon instead of a packed day."
    day_plan = body["trip"]["trip_state"]["planner_state"]["day_plan"]
    assert day_plan[0]["pace"] == "relaxed"
    assert day_plan[0]["buffer_note"] == "Free afternoon after removing Ram Jhula."


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
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": 1},
        "planner_state": {
            "conversation_context": {"awaiting": "duration"},
            "places": [],
            "day_plan": [],
            "revision": 1,
        },
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
    state = {
        "stage": "planning", "active_agent": "guide",
        "trip_context": {
            "destinations": ["Rishikesh"], "duration_days": 1,
            "preferences": ["pilgrimage"], "exclusions": ["rafting"],
        },
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": [{"day_number": 1, "date": None, "places": ["Triveni Ghat"], "pace": "balanced", "buffer_note": None}],
            "revision": 4,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    payload = {"command": "approve_plan", "expected_version": 1,
               "idempotency_key": str(uuid4())}

    first = api_client.post(f"/trips/{trip['id']}/commands", json=payload)
    replay = api_client.post(f"/trips/{trip['id']}/commands", json=payload)

    assert first.status_code == 200
    assert replay.json() == first.json()
    # approve_plan is a deterministic Backend transition — Guide is never
    # invoked for it, since preserving the day plan unchanged needs no
    # judgment.
    assert len(engine.calls) == 0
    saved = first.json()["trip"]["trip_state"]
    assert saved["stage"] == "planned"
    assert saved["active_agent"] is None
    assert saved["planner_state"]["revision"] == 5
    frozen = saved["planner_state"]["frozen_plan"]
    assert frozen["guide_revision"] == 5
    assert frozen["guide_state"] == {
        "destinations": ["Rishikesh"],
        "duration_days": 1,
        "start_date": None,
        "places": ["Triveni Ghat"],
        "day_plan": [{"day_number": 1, "date": None, "places": ["Triveni Ghat"], "pace": "balanced", "buffer_note": None}],
    }

    rejected = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "traveler_message", "message": "Change the temple",
              "expected_version": 2, "idempotency_key": str(uuid4())},
    )
    assert rejected.status_code == 422
    assert len(engine.calls) == 0
    assert api_client.get(f"/trips/{trip['id']}").json()["version"] == 2


def _frozen_plan_trip_state(*, guide_revision=5, duration_days=1, start_date=None):
    guide_state = {
        "destinations": ["Rishikesh"],
        "duration_days": duration_days,
        "start_date": start_date,
        "places": ["Triveni Ghat"],
        "day_plan": [
            {"day_number": day, "date": None, "places": ["Triveni Ghat"] if day == 1 else [], "pace": "balanced", "buffer_note": None}
            for day in range(1, duration_days + 1)
        ],
    }
    return {
        "stage": "planned",
        "active_agent": None,
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": duration_days, "start_date": start_date},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": guide_state["day_plan"],
            "revision": guide_revision,
            "frozen_plan": {"guide_revision": guide_revision, "guide_state": guide_state},
        },
    }


def test_start_itinerary_requires_frozen_plan(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "planning", "active_agent": "guide",
        "trip_context": {"destination": "Rishikesh"},
        "planner_state": {"guide_session": {"revision": 1, "state": {
            "phase": "PLACES_DRAFT", "destinations": ["Rishikesh"],
            "duration_days": None, "start_date": None, "places": [],
            "day_plan": [], "preferences": [], "exclusions": [],
            "applied_changes": [], "pending_clarification": None,
        }}},
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "start_itinerary", "expected_version": 1,
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422
    assert engine.calls == []


def test_start_itinerary_invokes_atlas_from_frozen_plan(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = _frozen_plan_trip_state(guide_revision=5)
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "start_itinerary", "expected_version": 1,
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 200
    assert len(engine.calls) == 1
    saved = response.json()["trip"]["trip_state"]
    itinerary = saved["itinerary_state"]
    assert itinerary["status"] == "ready"
    assert itinerary["proposed_revision"] is None
    assert "history" not in itinerary  # archived history lives in itinerary_versions now (TWM-155)
    current = itinerary["current_version"]
    assert current["version"] == 1
    assert current["source_guide_revision"] == 5
    assert current["result"]["final_itinerary"]["trip_summary"]["destinations"] == ["Rishikesh"]
    assert current["result"]["final_itinerary"]["assumptions"] == [
        {
            "category": "dates",
            "detail": "Assumed a start date since none was confirmed.",
        }
    ]


def test_start_itinerary_is_idempotent_and_does_not_rerun_atlas(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = _frozen_plan_trip_state(guide_revision=5)
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    first = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "start_itinerary", "expected_version": 1,
              "idempotency_key": str(uuid4())},
    )
    second = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "start_itinerary", "expected_version": 2,
              "idempotency_key": str(uuid4())},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(engine.calls) == 1
    first_itinerary = first.json()["trip"]["trip_state"]["itinerary_state"]
    # The second call is a genuine no-op (itinerary already ready, apply_atlas
    # returns without touching itinerary_state) — the trimmed response
    # correctly omits an untouched branch, so fetch the persisted state to
    # confirm nothing changed instead of expecting it inline.
    assert "itinerary_state" not in second.json()["trip"]["trip_state"]
    persisted_itinerary = api_client.get(f"/trips/{trip['id']}").json()["trip_state"]["itinerary_state"]
    assert first_itinerary["current_version"]["result"] == persisted_itinerary["current_version"]["result"]


def test_start_itinerary_rejects_browser_supplied_plan_fields(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = _frozen_plan_trip_state(guide_revision=5)
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "start_itinerary", "expected_version": 1,
              "idempotency_key": str(uuid4()), "message": "please hurry"},
    )

    assert response.status_code == 422
    assert engine.calls == []


def _seed_ready_itinerary(api_client, repository, engine, *, guide_revision=5, duration_days=1):
    """Seeds a frozen plan, then drives the real start_itinerary command so the
    resulting itinerary_state.current_version has the exact same normalized
    shape confirm_logistics will later diff against (all Pydantic default
    fields present) — avoids a hand-written fixture drifting from the schema.
    """
    state = _frozen_plan_trip_state(guide_revision=guide_revision, duration_days=duration_days)
    trip = _create_seeded_trip(api_client, repository, trip_state=state)
    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "start_itinerary", "expected_version": 1, "idempotency_key": str(uuid4())},
    )
    assert response.status_code == 200
    return response.json()["trip"]


def _confirm_logistics_payload(**overrides):
    payload = {
        "command": "confirm_logistics", "expected_version": 1, "idempotency_key": str(uuid4()),
        "logistics_confirmation": {
            "type": "transport", "label": "Delhi to Rishikesh arrival",
            "detail": "Confirmed arrival at 2:00 PM via train 12050.",
            "day_number": 1, "reference": "PNR-12345", "notes": None,
        },
    }
    payload.update(overrides)
    return payload


def test_confirm_logistics_requires_existing_itinerary(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = _frozen_plan_trip_state(guide_revision=5)
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands", json=_confirm_logistics_payload(),
    )

    assert response.status_code == 422
    assert engine.calls == []


def test_confirm_logistics_persists_anchor_and_proposes_revision(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json=_confirm_logistics_payload(expected_version=trip["version"]),
    )

    assert response.status_code == 200
    assert len(engine.calls) == 2  # start_itinerary + confirm_logistics
    call_trip_state = engine.calls[1][1]
    assert call_trip_state["confirmed_anchors"] == [
        {"type": "transport", "label": "Delhi to Rishikesh arrival",
         "detail": "Confirmed arrival at 2:00 PM via train 12050.", "day_number": 1}
    ]
    saved = response.json()["trip"]["trip_state"]
    anchors = saved["logistics_state"]["anchors"]
    assert len(anchors) == 1
    assert anchors[0]["type"] == "transport"
    assert anchors[0]["confirmed_at_version"] == 1

    proposed = saved["itinerary_state"]["proposed_revision"]
    assert proposed["base_version"] == 1
    assert proposed["version"] == 2
    assert proposed["affected_days"] == [1]
    assert proposed["changes"] == ["Day 1: Delhi to Rishikesh arrival (confirmed)"]
    assert proposed["triggered_by"]["type"] == "transport"
    assert saved["itinerary_state"]["current_version"]["version"] == 1


def test_confirm_logistics_second_call_replaces_pending_proposal(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    first = api_client.post(
        f"/trips/{trip['id']}/commands",
        json=_confirm_logistics_payload(expected_version=trip["version"]),
    )
    second = api_client.post(
        f"/trips/{trip['id']}/commands",
        json=_confirm_logistics_payload(
            expected_version=first.json()["trip"]["version"], idempotency_key=str(uuid4()),
            logistics_confirmation={
                "type": "stay", "label": "Riverside stay", "detail": "Confirmed check-in Day 2.",
                "day_number": 2, "reference": None, "notes": None,
            },
        ),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(engine.calls) == 3  # start_itinerary + 2x confirm_logistics
    second_call_trip_state = engine.calls[2][1]
    assert len(second_call_trip_state["confirmed_anchors"]) == 2

    saved = second.json()["trip"]["trip_state"]
    assert len(saved["logistics_state"]["anchors"]) == 2
    proposed = saved["itinerary_state"]["proposed_revision"]
    assert proposed["version"] == 2
    assert set(proposed["affected_days"]) == {1, 2}
    assert saved["itinerary_state"]["current_version"]["version"] == 1


def test_accept_itinerary_revision_activates_and_archives(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    confirm = api_client.post(
        f"/trips/{trip['id']}/commands",
        json=_confirm_logistics_payload(expected_version=trip["version"]),
    )
    accept = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "accept_itinerary_revision",
              "expected_version": confirm.json()["trip"]["version"],
              "idempotency_key": str(uuid4())},
    )

    assert confirm.status_code == 200
    assert accept.status_code == 200
    saved = accept.json()["trip"]["trip_state"]
    itinerary = saved["itinerary_state"]
    assert itinerary["proposed_revision"] is None
    assert itinerary["current_version"]["version"] == 2
    assert "history" not in itinerary  # archived history lives in itinerary_versions now (TWM-155)
    archived = repository.itinerary_versions[UUID(trip["id"])]
    assert len(archived) == 1
    assert archived[0].version == 1
    fetched = api_client.get(f"/trips/{trip['id']}/itinerary-versions")
    assert fetched.status_code == 200
    [version_1] = fetched.json()["versions"]
    assert version_1["version"] == 1
    assert version_1["source_guide_revision"] == 5
    assert version_1["days"] == [
        {"day_number": 1, "title": "Day 1"},
        {"day_number": 2, "title": "Day 2"},
    ]


def test_itinerary_versions_empty_before_any_accepted_revision(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    response = api_client.get(f"/trips/{trip['id']}/itinerary-versions")

    assert response.status_code == 200
    assert response.json() == {"versions": []}


def test_itinerary_versions_404_for_unknown_trip(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    response = api_client.get(f"/trips/{uuid4()}/itinerary-versions")

    assert response.status_code == 404


def test_accept_itinerary_revision_requires_pending_proposal(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "accept_itinerary_revision", "expected_version": trip["version"],
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422


def test_keep_current_itinerary_discards_proposal_but_keeps_anchor(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    confirm = api_client.post(
        f"/trips/{trip['id']}/commands",
        json=_confirm_logistics_payload(expected_version=trip["version"]),
    )
    keep = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "keep_current_itinerary",
              "expected_version": confirm.json()["trip"]["version"],
              "idempotency_key": str(uuid4())},
    )

    assert confirm.status_code == 200
    assert keep.status_code == 200
    saved = keep.json()["trip"]["trip_state"]
    itinerary = saved["itinerary_state"]
    assert itinerary["proposed_revision"] is None
    assert itinerary["current_version"]["version"] == 1
    assert "history" not in itinerary
    assert not repository.itinerary_versions.get(UUID(trip["id"]))  # keep discards, never archives
    # keep_current_itinerary only touches itinerary_state — logistics_state
    # (the anchor from the earlier confirm_logistics call) is correctly
    # absent from this trimmed response; confirm it's still persisted.
    assert "logistics_state" not in saved
    persisted_anchors = api_client.get(f"/trips/{trip['id']}").json()["trip_state"]["logistics_state"]["anchors"]
    assert len(persisted_anchors) == 1


def test_keep_current_itinerary_requires_pending_proposal(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeAtlasLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_ready_itinerary(api_client, repository, engine, guide_revision=5, duration_days=2)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "keep_current_itinerary", "expected_version": trip["version"],
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422


def test_approve_plan_rejects_wrong_phase_without_invoking_guide(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    # approve_plan is only valid once a day plan exists — places drafted but
    # no day plan built yet must be rejected deterministically, without ever
    # calling Guide.
    state = {
        "stage": "planning", "active_agent": "guide",
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": 1},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": [],
            "revision": 1,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "approve_plan", "expected_version": 1,
              "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422
    assert len(engine.calls) == 0
    persisted = api_client.get(f"/trips/{trip['id']}").json()
    assert persisted["version"] == 1
    assert persisted["trip_state"]["planner_state"]["places"] == ["Triveni Ghat"]
    assert persisted["trip_state"]["planner_state"]["day_plan"] == []


def test_day_plan_survives_a_backend_owned_clarification_round_trip(
    api_client: TestClient,
):
    """An ordinary (non-duration) ambiguity clarification leaves the day
    plan untouched — Guide's delta is empty on the asking turn, so there is
    nothing for Backend to preserve or restore; the places/day_plan already
    persisted simply never move."""
    repository = MemoryTripRepository()
    engine = FakeDayPlanClarificationEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    day_plan = [
        {"day_number": 1, "date": None, "places": ["Triveni Ghat"], "pace": "balanced", "buffer_note": None}
    ]
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": 1},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": day_plan,
            "revision": 1,
        },
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
    pending = clarification.json()["trip"]["trip_state"]["planner_state"]
    assert pending["day_plan"] == day_plan
    assert pending["places"] == ["Triveni Ghat"]
    assert pending["revision"] == 2

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
    resumed = resolved.json()["trip"]["trip_state"]["planner_state"]
    assert resumed["revision"] == 3
    assert resumed["day_plan"] == day_plan
    assert resolved.json()["trip"]["trip_state"]["trip_context"]["preferences"] == ["adventure"]


def test_approve_places_requires_duration_without_invoking_guide(
    api_client: TestClient,
):
    """Backend guards approve_places on trip_context.duration_days being
    known before Guide is ever called — under the old contract Guide itself
    fielded this as a soft mid-flow clarification; now it's a hard
    precondition the traveler must resolve first."""
    repository = MemoryTripRepository()
    engine = FakeGuideLifecycleEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destinations": ["Rishikesh"]},
        "planner_state": {
            "conversation_context": {"awaiting": "duration"},
            "places": ["Triveni Ghat"],
            "day_plan": [],
            "revision": 1,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "approve_places",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )
    assert response.status_code == 422
    assert len(engine.calls) == 0


class FakeGuidePreferenceEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        return AgentExecution(
            response={
                "message": "Noted.",
                "state_delta": {
                    "trip_context": {"preferences": ["PILGRIMAGE", "quiet"]},
                },
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


def test_guide_preference_delta_unions_with_earlier_specialist_context(
    api_client: TestClient,
):
    """trip_context.preferences/exclusions accumulate as a case-insensitive
    union across specialists and turns instead of the later write silently
    overwriting the earlier one — same accumulation Scout/Meridian already
    get from merge_trip_context."""
    repository = MemoryTripRepository()
    engine = FakeGuidePreferenceEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {
            "destinations": ["Rishikesh"],
            "duration_days": 3,
            "preferences": ["pilgrimage", "relaxed"],
        },
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": [],
            "revision": 1,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "I'd also like it to be quiet.",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    preferences = response.json()["trip"]["trip_state"]["trip_context"]["preferences"]
    # "PILGRIMAGE" (Guide) case-insensitively dedupes against "pilgrimage"
    # (already there); "relaxed" (already there) survives untouched; "quiet"
    # (Guide) is newly added.
    assert preferences == ["pilgrimage", "relaxed", "quiet"]


class FakeGuideMisallocatedDayPlanEngine(FakeCommandEngine):
    async def guide(self, trip_state, message):
        self.calls.append(("guide", trip_state, message))
        return AgentExecution(
            response={
                "message": "Here is your day plan.",
                "state_delta": {
                    "planner_state": {
                        "day_plan": [
                            {
                                "day_number": 1,
                                "date": None,
                                # Allocates a place never approved — the
                                # exact failure mode the redesign's day-plan
                                # consistency check exists to catch.
                                "places": ["Unapproved Place"],
                                "pace": "balanced",
                                "buffer_note": None,
                            }
                        ],
                    },
                },
            },
            prompt_release=PromptRelease("guide", "1.0.0", "test"),
        )


def test_approve_places_rejects_day_plan_allocating_an_unapproved_place(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakeGuideMisallocatedDayPlanEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = {
        "stage": "planning",
        "active_agent": "guide",
        "trip_context": {"destinations": ["Rishikesh"], "duration_days": 1},
        "planner_state": {
            "conversation_context": {"awaiting": None},
            "places": ["Triveni Ghat"],
            "day_plan": [],
            "revision": 1,
        },
    }
    trip = _create_seeded_trip(api_client, repository, trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "approve_places",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422
    persisted = api_client.get(f"/trips/{trip['id']}").json()
    assert persisted["version"] == 1
    assert persisted["trip_state"]["planner_state"]["day_plan"] == []


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
    # advisor_state is never included in a command response (dead weight,
    # never read back); matcher_state is untouched by start_planning, so the
    # trimmed response correctly omits it too — only planner_state changed.
    assert "advisor_state" not in saved
    assert "matcher_state" not in saved
    assert saved["planner_state"]["revision"] == 1


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
    assert saved["planner_state"]["places"] == ["Triveni Ghat"]
    assert not saved["planner_state"].get("day_plan")


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


def test_scout_entry_invokes_scout_only(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeCommandEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "scout_entry",
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


def test_scout_entry_hands_off_to_meridian_in_same_command(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Mountains"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "scout_entry",
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


def test_scout_entry_requires_message(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: FakeCommandEngine()
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={"command": "scout_entry", "expected_version": 1, "idempotency_key": str(uuid4())},
    )

    assert response.status_code == 422


def test_discover_entry_passes_the_travelers_first_message_directly_to_meridian(
    api_client: TestClient,
):
    # "Not sure yet" shows a hardcoded first question (e.g. "where are you
    # traveling from?") before any Backend call — the traveler's reply is
    # this message, and it must reach Meridian directly, never Scout.
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "discover_entry",
            "message": "Delhi",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["meridian"]
    assert engine.calls[0][2] == "Delhi"


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


def test_matcher_round_archives_to_dedicated_table_not_trip_state(api_client: TestClient):
    """TWM-153: any terminal matcher outcome (including a failure status,
    not just SUCCESS) is archived to matcher_recommendations, never appended
    to trip_state — and idempotent replay does not duplicate the row."""
    repository = MemoryTripRepository()
    engine = FakeHandoffEngine()  # meridian returns HARD_FAIL
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = api_client.post("/trips", json={"title": "Trip"}).json()
    trip_id = UUID(trip["id"])
    payload = {"command": "discover_entry", "message": "Delhi", "expected_version": 1, "idempotency_key": str(uuid4())}

    first = api_client.post(f"/trips/{trip['id']}/commands", json=payload)
    replay = api_client.post(f"/trips/{trip['id']}/commands", json=payload)

    assert first.status_code == 200
    assert "recommendations" not in first.json()["trip"]["trip_state"].get("matcher_state", {})
    assert replay.json() == first.json()
    assert len(repository.recommendations[trip_id]) == 1
    archived = repository.recommendations[trip_id][0]
    assert archived.status == "HARD_FAIL"
    assert archived.version == 1


def test_get_recommendations_404_before_any_matcher_round(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.get(f"/trips/{trip['id']}/recommendations")

    assert response.status_code == 404


def test_get_recommendations_404_for_unknown_trip(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    response = api_client.get(f"/trips/{uuid4()}/recommendations")

    assert response.status_code == 404


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
    # Confirms known_destination_entry writes destinations before Guide is
    # ever invoked, independent of whatever Guide's own (fake) response
    # then does to that same shared trip_context field.
    assert engine.calls[0][1]["trip_context"]["destinations"] == ["Goa"]
    saved = response.json()["trip"]
    assert saved["version"] == 2
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


class FakeMoreLikeThisEngine(FakeCommandEngine):
    async def meridian(self, trip_state, message):
        self.calls.append(("meridian", trip_state, message))
        return AgentExecution(
            response={
                "status": "SUCCESS",
                "message": "Here is another relaxed beach option close to Goa.",
                "state_delta": {"trip_context": {}, "matcher_state": {
                    "conversation_context": {"awaiting": None}
                }},
                "trip_type": "single",
                "traveler_criteria": [{
                    "id": "pace", "label": "Relaxed pace",
                    "requirement_type": "PREFERENCE", "source_context_paths": ["travel_style.pace"],
                }],
                "options": [{
                    "rank": 1, "type": "single", "name": "Gokarna Coast",
                    "destination_id": "gokarna",
                    "summary": "A quieter beach town close to Goa's coastline.",
                    "evaluations": [{
                        "criterion_id": "pace", "outcome": "MATCH",
                        "conclusion": "Gokarna keeps a relaxed pace similar to Goa.",
                        "details": [{"type": "bullets", "items": ["Fewer crowds than Goa."]}],
                    }],
                }],
            },
            prompt_release=PromptRelease("meridian", "1.0.0", "test"),
        )


def _recommended_state_with_goa_option():
    return {
        "stage": "recommended",
        "active_agent": None,
        "trip_context": {},
    }


def _seed_recommended_trip_with_goa_option(api_client, repository):
    trip = _create_seeded_trip(
        api_client, repository, trip_state=_recommended_state_with_goa_option()
    )
    _seed_recommendation(repository, UUID(trip["id"]), options=[
        {"rank": 1, "type": "single", "destination_id": "goa", "name": "Goa"}
    ])
    return trip


def test_more_like_this_refines_around_the_referenced_option(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeMoreLikeThisEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_recommended_trip_with_goa_option(api_client, repository)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "more_like_this",
            "refinement": {
                "type": "MORE_LIKE_THIS",
                "reference": {"type": "single", "id": "goa"},
                "instructions": "Somewhere quieter",
            },
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["meridian"]
    forwarded_trip_state = engine.calls[0][1]
    assert forwarded_trip_state["matcher_state"]["refinement"] == {
        "type": "MORE_LIKE_THIS",
        "reference": {"type": "single", "id": "goa"},
        "instructions": "Somewhere quieter",
    }
    assert engine.calls[0][2] == "Somewhere quieter"
    saved = response.json()["trip"]["trip_state"]
    assert saved["stage"] == "recommended"
    # recommendations live in the matcher_recommendations table now (TWM-153),
    # not in trip_state — check the archived round and the lazy GET endpoint.
    latest_round = repository.recommendations[UUID(trip["id"])][-1]
    assert latest_round.options[0]["destination_id"] == "gokarna"
    fetched = api_client.get(f"/trips/{trip['id']}/recommendations")
    assert fetched.status_code == 200
    assert fetched.json()["options"][0]["destination_id"] == "gokarna"
    assert fetched.json()["version"] == 2


def test_more_like_this_rejects_unknown_reference_without_mutating_state(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    engine = FakeMoreLikeThisEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _seed_recommended_trip_with_goa_option(api_client, repository)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "more_like_this",
            "refinement": {
                "type": "MORE_LIKE_THIS",
                "reference": {"type": "single", "id": "an-unknown-injected-id"},
            },
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert engine.calls == []
    assert repository.trips[UUID(trip["id"])].version == 1


def test_more_like_this_requires_refinement_field(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: FakeMoreLikeThisEngine()
    trip = _seed_recommended_trip_with_goa_option(api_client, repository)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "more_like_this",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422


def test_refinement_field_rejected_for_other_commands(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: FakeCommandEngine()
    trip = api_client.post("/trips", json={"title": "Trip"}).json()

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "hello",
            "refinement": {
                "type": "MORE_LIKE_THIS",
                "reference": {"type": "single", "id": "goa"},
            },
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422


def test_guide_reversal_reopens_destination_discovery_in_same_command(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideReversalEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _create_seeded_trip(api_client, repository, title="Goa", trip_state=_seeded_guide_places_state())

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Actually, let's not do Goa, suggest somewhere else.",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["guide", "meridian"]
    saved = response.json()["trip"]
    assert saved["version"] == 2
    trip_state = saved["trip_state"]
    assert trip_state["stage"] == "matching"
    assert trip_state["active_agent"] == "meridian"
    assert "destination" not in trip_state["trip_context"]
    assert "destinations" not in trip_state["trip_context"]
    superseded = trip_state["planner_state"]["superseded_planner_states"]
    assert len(superseded) == 1
    assert superseded[0]["destination_context"] == ["Goa"]
    assert superseded[0]["planner_state"]["places"] == ["Baga Beach"]
    assert trip_state["planner_state"]["places"] == []


def test_guide_ordinary_edit_does_not_trigger_reversal(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideOrdinaryEditEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    trip = _create_seeded_trip(api_client, repository, title="Goa", trip_state=_seeded_guide_places_state())

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Also add Anjuna Beach.",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert [call[0] for call in engine.calls] == ["guide"]
    trip_state = response.json()["trip"]["trip_state"]
    assert trip_state["stage"] == "planning"
    assert trip_state["active_agent"] == "guide"
    assert trip_state["trip_context"]["destinations"] == ["Goa"]
    assert "superseded_planner_states" not in trip_state["planner_state"]
    assert trip_state["planner_state"]["places"] == ["Baga Beach", "Anjuna Beach"]


def test_guide_reversal_is_rejected_once_the_plan_is_frozen(api_client: TestClient):
    repository = MemoryTripRepository()
    engine = FakeGuideReversalEngine()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_engine] = lambda: engine
    state = _seeded_guide_places_state()
    state["stage"] = "planned"
    state["active_agent"] = None
    state["planner_state"]["frozen_plan"] = {
        "guide_revision": 1,
        "guide_state": {
            "destinations": state["trip_context"]["destinations"],
            "duration_days": None,
            "start_date": None,
            "places": state["planner_state"]["places"],
            "day_plan": [],
        },
    }
    trip = _create_seeded_trip(api_client, repository, title="Goa", trip_state=state)

    response = api_client.post(
        f"/trips/{trip['id']}/commands",
        json={
            "command": "traveler_message",
            "message": "Actually, let's not do Goa, suggest somewhere else.",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
        },
    )

    assert response.status_code == 422
    assert engine.calls == []
