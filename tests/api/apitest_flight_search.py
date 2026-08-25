"""API coverage for the trip-owned live flight-search boundary (TWM-144)."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.dependencies import (
    get_current_user,
    get_flight_search_service,
    get_logger,
    get_trip_persistence,
)
from twm.main import app
from twm.persistence.contracts import GuestSession, TripRecord
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings
from twm.services.flight_search import FlightSearchService
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


class MemoryTripRepository:
    """Minimal in-memory TripRepository fake covering only what the
    flight-search boundary touches: guest resolution and trip lookup."""

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


def _service(repository):
    return TripPersistenceService(repository, DatabaseSettings(url=None, guest_cookie_secure=False))


def _create_trip(api_client: TestClient, *, title: str = "Rishikesh") -> str:
    created = api_client.post("/trips", json={"title": title, "trip_context": {"destination": "Test"}})
    assert created.status_code == 201
    return created.json()["id"]


VALID_REQUEST = {
    "origin_iata": "del",
    "destination_iata": "bom",
    "trip_type": "round_trip",
    "departure_date": "2026-09-10",
    "return_date": "2026-09-17",
    "travelers": {"adults": 2, "children": 0, "infants": 0},
}


def test_ready_request_returns_typed_unavailable_outcome_no_provider_yet(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)
    original_version = repository.trips[UUID(trip_id)].version

    response = api_client.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["unavailable"]["code"] == "provider_not_configured"
    assert body["offers"] == []
    assert body["clarification"] is None
    assert body["failure"] is None
    assert "queried_at" in body

    # IATA codes normalize to upper case (StringConstraints to_upper).
    # This is only observable indirectly here since the request accepted
    # lowercase input without a 422 and produced a deterministic outcome.

    # Read/query boundary: never mutates the trip's optimistic-concurrency version.
    assert repository.trips[UUID(trip_id)].version == original_version


def test_missing_route_and_date_returns_typed_clarification_not_a_crash(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={"travelers": {"adults": 1, "children": 0, "infants": 0}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "clarification_needed"
    # trip_type defaults to one_way (TWM-196: visible gateway legs are
    # directional rows, not an implicit round trip), so return_date is not
    # in this list unless the caller explicitly asks for round_trip.
    # departure_date is also not required (TWM-196): a route-and-traveler
    # search with no date at all still resolves to a flexible/latest
    # search once route/traveler inputs are supplied, not a hard block.
    assert set(body["clarification"]["missing_fields"]) == {
        "origin",
        "destination",
    }
    assert body["offers"] == []
    assert body["unavailable"] is None


def test_missing_return_date_is_reported_only_for_round_trip(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "BLR",
            "destination_iata": "BBI",
            "trip_type": "round_trip",
            "departure_date": "2027-01-10",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "clarification_needed"
    assert body["clarification"]["missing_fields"] == ["return_date"]


def test_one_way_request_never_requires_return_date(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "BLR",
            "destination_iata": "BBI",
            "departure_date": "2027-01-10",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    # No provider configured in this fixture, so this resolves to a typed
    # "unavailable" outcome rather than a clarification -- proving
    # return_date was never treated as missing for the default one_way
    # trip_type.
    assert body["status"] == "unavailable"
    assert body["unavailable"]["code"] == "provider_not_configured"


def test_place_endpoints_resolve_to_iata_via_backend(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_place": "Bangalore",
            "destination_place": "Bhubaneswar",
            "departure_date": "2027-01-10",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["unavailable"]["code"] == "provider_not_configured"
    assert body["origin_resolved"]["iata"] == "BLR"
    assert body["destination_resolved"]["iata"] == "BBI"


def test_bangalore_to_delhi_resolves_both_airports_not_a_clarification(
    api_client: TestClient,
):
    # Regression for a manual TWM-196 verification finding: bare "Delhi"
    # returned clarification_needed on the destination side because
    # OurAirports only carries "New Delhi" as a municipality/keyword match.
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_place": "Bangalore",
            "destination_place": "Delhi",
            "departure_date": "2027-01-10",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] != "clarification_needed"
    assert body["origin_resolved"]["iata"] == "BLR"
    assert body["destination_resolved"]["iata"] == "DEL"


def test_unresolvable_place_endpoint_is_a_typed_clarification_not_a_guess(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_place": "Nowhereville",
            "destination_place": "Bhubaneswar",
            "departure_date": "2027-01-10",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "clarification_needed"
    assert body["clarification"]["missing_fields"] == ["origin"]
    assert body["origin_resolved"] is None
    assert body["destination_resolved"]["iata"] == "BBI"


def test_missing_travelers_is_a_typed_clarification_field(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "trip_type": "one_way",
            "departure_date": "2026-09-10",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "clarification_needed"
    assert body["clarification"]["missing_fields"] == ["travelers"]


def test_malformed_payload_with_extra_field_is_rejected_with_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={**VALID_REQUEST, "cabin": "business"},
    )
    assert response.status_code == 422


def test_same_origin_and_destination_is_rejected_with_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={**VALID_REQUEST, "destination_iata": "DEL"},
    )
    assert response.status_code == 422


def test_departure_date_and_departure_month_together_is_rejected_with_422(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={**VALID_REQUEST, "departure_month": "2026-09"},
    )
    assert response.status_code == 422


def test_exact_departure_date_yields_exact_date_precision(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_date": "2027-01-10",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["date_precision"] == "exact"


def test_departure_month_yields_month_date_precision_not_a_hard_block(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_month": "2027-01",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    # Never a clarification_needed: month-only is a genuine, non-blocking
    # search precision (TWM-196), not a missing_input.
    assert body["status"] == "unavailable"
    assert body["date_precision"] == "month"


def test_no_date_at_all_yields_flexible_date_precision_not_a_hard_block(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["date_precision"] == "flexible"


def test_malformed_departure_month_is_rejected_with_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={
            "origin_iata": "DEL",
            "destination_iata": "BOM",
            "departure_month": "2027-13",
            "travelers": {"adults": 1, "children": 0, "infants": 0},
        },
    )
    assert response.status_code == 422


def test_one_way_trip_with_return_date_is_rejected_with_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={**VALID_REQUEST, "trip_type": "one_way"},
    )
    assert response.status_code == 422


def test_unknown_trip_returns_404(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    response = api_client.post(
        f"/trips/{uuid4()}/flight-search",
        json=VALID_REQUEST,
    )
    assert response.status_code == 404


def test_guest_cannot_search_flights_on_another_guests_trip():
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as owner, TestClient(app) as stranger:
        trip_id = _create_trip(owner)
        response = stranger.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)
        assert response.status_code == 404
    app.dependency_overrides.clear()


def test_repeat_identical_request_is_idempotent_and_safe(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    trip_id = _create_trip(api_client)

    first = api_client.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)
    second = api_client.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)
    assert first.status_code == 200
    assert second.status_code == 200
    first_body, second_body = first.json(), second.json()
    assert first_body["status"] == second_body["status"] == "unavailable"
    assert first_body["unavailable"] == second_body["unavailable"]
    # Trip stays untouched by either call — no duplicate side effects.
    assert repository.trips[UUID(trip_id)].version == 1


class _FakeConfiguredAdapter:
    """Fake AviasalesAdapter used to drive the provider-configured path
    through the HTTP boundary without any real network call."""

    async def fetch_cheapest_prices(self, **kwargs):
        return (
            [
                {
                    "price": 5000,
                    "airline": "AI",
                    "flight_number": 101,
                    "departure_at": "2026-09-10T10:00:00+00:00",
                    "return_at": "2026-09-17T10:00:00+00:00",
                    "expires_at": "2026-08-20T00:00:00+00:00",
                }
            ],
            datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc),
        )

    async def fetch_stop_count_hint(self, **kwargs):
        return None


def test_configured_provider_returns_offer_status_with_no_raw_payload(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=False, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        InMemorySink(),
    )
    app.dependency_overrides[get_flight_search_service] = lambda: FlightSearchService(
        logger=logger, adapter=_FakeConfiguredAdapter(), currency="USD"
    )

    trip_id = _create_trip(api_client)
    response = api_client.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    assert len(body["offers"]) == 1
    offer = body["offers"][0]
    assert offer["money"]["currency"] == "USD"
    assert offer["money"]["group_total_is_approximate"] is True
    assert offer["baggage"]["checked_bag_included"] is None
    assert offer["fare_conditions"]["refundable"] is None
    assert offer["provenance"]["provider_name"] == "aviasales"
    assert "url" not in offer["provenance"]
    assert offer["is_recommended"] is True
    assert body["unavailable"] is None


class _FakeMultiOfferAdapter:
    """Fake AviasalesAdapter returning multiple offers at different prices,
    to drive ranking (TWM-146) through the full HTTP boundary."""

    async def fetch_cheapest_prices(self, **kwargs):
        return (
            [
                {
                    "price": 9000,
                    "airline": "AI",
                    "flight_number": 201,
                    "departure_at": "2026-09-10T10:00:00+00:00",
                    "return_at": "2026-09-17T10:00:00+00:00",
                    "expires_at": "2026-08-20T00:00:00+00:00",
                },
                {
                    "price": 3000,
                    "airline": "AI",
                    "flight_number": 202,
                    "departure_at": "2026-09-10T14:00:00+00:00",
                    "return_at": "2026-09-17T14:00:00+00:00",
                    "expires_at": "2026-08-20T00:00:00+00:00",
                },
            ],
            datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc),
        )

    async def fetch_stop_count_hint(self, **kwargs):
        return None


def test_multiple_offers_are_ranked_cheapest_first_with_top_offer_marked(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=False, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        InMemorySink(),
    )
    app.dependency_overrides[get_flight_search_service] = lambda: FlightSearchService(
        logger=logger, adapter=_FakeMultiOfferAdapter(), currency="USD"
    )

    trip_id = _create_trip(api_client)
    response = api_client.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offer"
    offers = body["offers"]
    assert len(offers) == 2
    group_totals = [offer["money"]["group_total_minor_units"] for offer in offers]
    assert group_totals == sorted(group_totals)
    assert offers[0]["is_recommended"] is True
    assert offers[1]["is_recommended"] is False


def test_readiness_and_unavailable_events_are_logged_with_trip_id(api_client: TestClient):
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

    trip_id = _create_trip(api_client)
    api_client.post(f"/trips/{trip_id}/flight-search", json=VALID_REQUEST)

    events = [event["event"] for event in sink.events]
    assert "be.flight_search.requested" in events
    assert "be.flight_search.unavailable" in events
    unavailable_event = next(e for e in sink.events if e["event"] == "be.flight_search.unavailable")
    assert unavailable_event["fields"]["trip_id"] == trip_id

    sink.events.clear()
    api_client.post(
        f"/trips/{trip_id}/flight-search",
        json={"travelers": {"adults": 1, "children": 0, "infants": 0}},
    )
    events = [event["event"] for event in sink.events]
    assert "be.flight_search.readiness_rejected" in events
    rejection_event = next(
        e for e in sink.events if e["event"] == "be.flight_search.readiness_rejected"
    )
    assert rejection_event["fields"]["trip_id"] == trip_id
    assert "origin" in rejection_event["fields"]["missing_fields"]
