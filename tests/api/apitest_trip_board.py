"""API coverage for GET /trips/{trip_id}/board (TWM-202) — the composed
Trip Board endpoint, using the real (deterministic) TrustedActionService
so feasibility results reflect the actual bounded-table calibration."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.dependencies import get_current_user, get_logger, get_trip_persistence, get_trusted_action_service
from twm.main import app
from twm.persistence.contracts import GuestSession, ItineraryVersionRecord, TripRecord
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings
from twm.services.trusted_action import TrustedActionService, TrustedActionSettings
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


class MemoryTripRepository:
    def __init__(self):
        self.guests = {}
        self.trips = {}

    async def resolve_guest(self, token_hash, lifetime_days):
        guest = self.guests.get(token_hash)
        if guest and guest.expires_at > datetime.now(timezone.utc):
            return guest
        return None

    async def create_guest(self, token_hash, lifetime_days):
        guest = GuestSession(uuid4(), datetime.now(timezone.utc) + timedelta(days=lifetime_days))
        self.guests[token_hash] = guest
        return guest

    async def create_trip(self, guest_id, user_id, title, product_mode, trip_state, ui_state):
        now = datetime.now(timezone.utc)
        trip = TripRecord(uuid4(), guest_id, user_id, title, product_mode, trip_state, ui_state, 1, now, now)
        self.trips[trip.id] = trip
        return trip

    async def get_trip(self, owner, trip_id):
        trip = self.trips.get(trip_id)
        if trip is None:
            return None
        if owner.user_id is not None:
            return trip if trip.user_id == owner.user_id else None
        return trip if trip.guest_session_id == owner.guest_session_id and trip.user_id is None else None

    async def get_current_itinerary(self, owner, trip_id):
        trip = await self.get_trip(owner, trip_id)
        if not trip:
            return None
        itinerary = trip.trip_state.get("itinerary_state") or {}
        current = itinerary.get("current_version")
        if not current:
            return None
        return ItineraryVersionRecord(
            trip_id=trip_id, version=current["version"], source_guide_revision=current["source_guide_revision"],
            result=current["result"], created_at=datetime.now(timezone.utc),
        )


def _service(repository):
    return TripPersistenceService(repository, DatabaseSettings(url=None, guest_cookie_secure=False))


def logger_for_test():
    return TelemetryLogger(TelemetrySettings(True, "test", PayloadMode.FULL, 16_384), InMemorySink())


def _create_trip(api_client: TestClient, repository: MemoryTripRepository, trip_state: dict) -> str:
    created = api_client.post("/trips", json={"title": "Trip", "trip_context": {"destination": "Test"}})
    trip_id = UUID(created.json()["id"])
    from dataclasses import replace
    repository.trips[trip_id] = replace(repository.trips[trip_id], trip_state=trip_state)
    return created.json()["id"]


def _reference():
    return {"status": "GENERAL_GUIDANCE"}


def _ready_trip_state(*, origin_city="Delhi", booking_setup=None):
    trip_context = {"origin_city": origin_city, "destinations": ["Chennai"]}
    return {
        "stage": "planned",
        "trip_context": trip_context,
        "booking_setup": booking_setup or {},
        "planner_state": {"frozen_plan": {"guide_revision": 1, "guide_state": {}}},
        "itinerary_state": {
            "status": "ready",
            "current_version": {
                "version": 1,
                "source_guide_revision": 1,
                "result": {
                    "final_itinerary": {
                        "trip_summary": {
                            "title": "Chennai Trip", "destinations": ["Chennai"], "trip_duration": 2,
                            "overview": "Overview.", "route_rationale": "Rationale.",
                        },
                        "days": [
                            {
                                "day_number": 1, "title": "Arrival", "primary_location": "Chennai",
                                "summary": "Summary.",
                                "notes": [
                                    {
                                        "category": "Weather",
                                        "title": "Carry layers",
                                        "detail": "Guidance.",
                                        "reference": _reference(),
                                    }
                                ],
                                "timeline": [
                                    {
                                        "kind": "TRAVEL", "title": "Travel from Delhi to Chennai",
                                        "location": "Delhi to Chennai", "detail": "Onward travel.",
                                        "from_city": "Delhi", "to_city": "Chennai",
                                        "reference": _reference(),
                                    },
                                    {
                                        "kind": "ACTIVITY", "title": "Marina Beach", "location": "Chennai",
                                        "detail": "Walk along the beach.", "reference": _reference(),
                                    },
                                ],
                            },
                            {
                                "day_number": 2, "title": "Departure", "primary_location": "Chennai",
                                "summary": "Summary.",
                                "notes": [
                                    {
                                        "category": "Weather",
                                        "title": "Carry layers",
                                        "detail": "Guidance.",
                                        "reference": _reference(),
                                    }
                                ],
                                "timeline": [
                                    {
                                        "kind": "STAY", "title": "Chennai stay", "location": "Chennai",
                                        "detail": "Stay overnight.", "reference": _reference(),
                                    },
                                    {
                                        "kind": "TRAVEL", "title": "Travel from Chennai to Delhi",
                                        "location": "Chennai to Delhi", "detail": "Return travel.",
                                        "from_city": "Chennai", "to_city": "Delhi",
                                        "reference": _reference(),
                                    },
                                ],
                            },
                        ],
                        "budget_summary": {
                            "currency": "INR",
                            "lines": [{"category": "Travel", "amount_low": 1000, "amount_high": 2000, "note": "Estimated transit cost."}],
                            "budget_fit": "Fits within a typical budget.",
                        },
                        "practical_notes": [], "sources": [], "assumptions": [],
                    },
                    "unresolved": [],
                    "agent_meta": {"agent": "atlas", "prompt_version": "1.0.0"},
                },
            },
        },
    }


def _override_dependencies(repository: MemoryTripRepository):
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_logger] = logger_for_test
    app.dependency_overrides[get_current_user] = lambda: None
    app.dependency_overrides[get_trusted_action_service] = lambda: TrustedActionService(
        logger=logger_for_test(),
        settings=TrustedActionSettings(ixigo_affiliate_id=None, travelpayouts_marker=None),
    )


def test_get_trip_board_composes_gateway_legs_with_real_feasibility(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_dependencies(repository)
    trip_id = _create_trip(api_client, repository, _ready_trip_state())

    response = api_client.get(f"/trips/{trip_id}/board")

    assert response.status_code == 200
    board = response.json()
    assert board["version"] == 1
    assert len(board["days"]) == 2

    outbound = board["days"][0]["items"][0]
    assert outbound["kind"] == "TRAVEL"
    assert outbound["is_gateway_leg"] is True
    modes = {entry["mode"] for entry in outbound["feasible_modes"]}
    assert "flight" in modes  # Delhi -> Chennai is a known long-distance pair

    activity = board["days"][0]["items"][1]
    assert activity["is_gateway_leg"] is False
    assert activity["feasible_modes"] is None

    inbound = board["days"][1]["items"][1]
    assert inbound["is_gateway_leg"] is True


def test_get_trip_board_has_no_trip_level_date_and_is_flexible_without_search_prefs(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_dependencies(repository)
    trip_id = _create_trip(api_client, repository, _ready_trip_state())

    response = api_client.get(f"/trips/{trip_id}/board")

    assert response.status_code == 200
    board = response.json()
    # No trip-level calendar anchor: days carry no date, and every bookable
    # entity stays date-flexible until a per-entity search pref is set.
    assert "date" not in board["days"][0]
    outbound = board["days"][0]["items"][0]
    inbound = board["days"][1]["items"][1]
    assert outbound["date_precision"] == "flexible"
    assert outbound["departure_date"] is None
    assert outbound["date_source"] == "none"
    assert inbound["date_source"] == "none"
    assert board["stay_segments"] == [
        {
            "id": f"{trip_id}:stay:2:2:chennai",
            "location": "Chennai",
            "start_day_number": 2,
            "end_day_number": 2,
            "nights": 1,
            "date_precision": "flexible",
            "checkin_date": None,
            "checkout_date": None,
            "departure_month": None,
            "date_source": "none",
            "board_item_ids": [board["days"][1]["items"][0]["id"]],
        }
    ]


def test_get_trip_board_applies_a_per_entity_search_pref(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_dependencies(repository)
    trip_id = _create_trip(api_client, repository, _ready_trip_state())
    segment_id = f"{trip_id}:stay:2:2:chennai"
    trip_state = _ready_trip_state(
        booking_setup={
            "search_prefs": {"stays": {segment_id: {"precision": "exact", "date": "2026-06-10"}}}
        }
    )
    from dataclasses import replace
    repository.trips[UUID(trip_id)] = replace(repository.trips[UUID(trip_id)], trip_state=trip_state)

    board = api_client.get(f"/trips/{trip_id}/board").json()
    segment = board["stay_segments"][0]
    assert segment["checkin_date"] == "2026-06-10"
    assert segment["checkout_date"] == "2026-06-11"
    assert segment["date_precision"] == "exact"
    assert segment["date_source"] == "search_pref"


def test_get_trip_board_returns_404_for_unknown_trip(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_dependencies(repository)

    response = api_client.get(f"/trips/{uuid4()}/board")

    assert response.status_code == 404


def test_get_trip_board_returns_404_before_any_itinerary_exists(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_dependencies(repository)
    trip_id = _create_trip(api_client, repository, {"stage": "planning", "trip_context": {}})

    response = api_client.get(f"/trips/{trip_id}/board")

    assert response.status_code == 404
