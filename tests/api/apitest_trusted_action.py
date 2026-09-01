"""API coverage for the trip-owned trusted-action resolution boundary
(TWM-131). Mirrors tests/api/apitest_flight_search.py's pattern."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from twm.dependencies import (
    get_current_user,
    get_logger,
    get_trip_persistence,
    get_trusted_action_service,
)
from twm.main import app
from twm.persistence.contracts import GuestSession, TripRecord
from twm.persistence.service import TripPersistenceService
from twm.persistence.settings import DatabaseSettings
from twm.services.trusted_action import TrustedActionService, TrustedActionSettings
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings


class MemoryTripRepository:
    """Minimal in-memory TripRepository fake covering only what the
    trusted-action boundary touches: guest resolution and trip lookup."""

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


def _create_trip(api_client: TestClient, *, title: str = "Kerala Backwaters") -> str:
    created = api_client.post("/trips", json={"title": title, "trip_context": {"destination": "Test"}})
    assert created.status_code == 201
    return created.json()["id"]


def _logger() -> TelemetryLogger:
    return TelemetryLogger(
        TelemetrySettings(
            enabled=False, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        InMemorySink(),
    )


VALID_FLIGHT_SEARCH_REDIRECT = {
    "action_type": "SEARCH_REDIRECT",
    "domain": "flight",
    "origin": "Delhi",
    "destination": "Mumbai",
    "trip_shape": "round_trip",
    "departure_date": "2026-09-10",
    "return_date": "2026-09-17",
    "traveler_count": 2,
}

VALID_CHECK_PRICES = {
    "action_type": "CHECK_PRICES",
    "domain": "flight",
    "origin": "Delhi",
    "destination": "Mumbai",
    "trip_shape": "one_way",
    "departure_date": "2026-09-10",
    "traveler_count": 1,
}


def _override_persistence(repository):
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)


def test_check_prices_resolves_to_internal_capability(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(f"/trips/{trip_id}/trusted-action", json=VALID_CHECK_PRICES)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["action"]["action_type"] == "CHECK_PRICES"
    assert body["action"]["internal_capability"] == "flight_search"
    assert body["action"]["target"] is None
    assert body["action"]["affiliate_disclosure"] is False
    assert body["missing_input"] is None
    assert body["unsupported_partner"] is None


def test_search_redirect_resolves_to_an_allowlisted_partner_target(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(f"/trips/{trip_id}/trusted-action", json=VALID_FLIGHT_SEARCH_REDIRECT)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    action = body["action"]
    assert action["action_type"] == "SEARCH_REDIRECT"
    # TWM-196: flight's approved SEARCH_REDIRECT partner is Aviasales
    # itself (Travelpayouts) — the same brand as the live-price path —
    # replacing the earlier ixigo placeholder.
    assert action["target"]["partner"] == "aviasales"
    assert action["affiliate_disclosure"] is True
    target_url = action["target"]["target_url"]
    # Aviasales' documented search-form deep link (TWM-196 P1 fix):
    # search.aviasales.com/flights/, IATA-based origin_iata/destination_iata
    # (Delhi/Mumbai resolve via Backend airport resolution), not a raw city
    # label or the generic resolver shape.
    assert target_url.startswith("https://search.aviasales.com/flights/?")
    assert "origin_iata=DEL" in target_url
    assert "destination_iata=BOM" in target_url
    assert "depart_date=2026-09-10" in target_url
    assert "return_date=2026-09-17" in target_url
    assert "one_way=false" in target_url
    assert "adults=2" in target_url
    assert "://" not in target_url[len("https://") :]


def test_missing_required_fields_returns_typed_missing_input(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={"action_type": "SEARCH_REDIRECT", "domain": "bus"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "missing_input"
    # departure_date and traveler_count are deliberately not required
    # (TWM-196/TWM-215): a safe affiliate/search-redirect URL can still be
    # built without either — see missing_required_fields' docstring.
    assert set(body["missing_input"]["missing_fields"]) == {
        "origin",
        "destination",
    }
    assert body["action"] is None


def test_affiliate_redirect_resolves_without_a_departure_date(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "flight",
            "origin": "Bangalore",
            "destination": "Bhubaneswar",
            "traveler_count": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    target_url = body["action"]["target"]["target_url"]
    assert target_url.startswith("https://search.aviasales.com/flights/?")
    assert "origin_iata=BLR" in target_url
    assert "destination_iata=BBI" in target_url
    assert "depart_date" not in target_url


def test_round_trip_still_requires_return_date(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "flight",
            "origin": "Bangalore",
            "destination": "Bhubaneswar",
            "trip_shape": "round_trip",
            "traveler_count": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "missing_input"
    assert body["missing_input"]["missing_fields"] == ["return_date"]


def test_one_way_is_the_default_trip_shape_and_never_requires_return_date(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "flight",
            "origin": "Bangalore",
            "destination": "Bhubaneswar",
            "traveler_count": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["action"]["trip_shape"] == "one_way"


def test_unsupported_preferred_partner_returns_typed_unsupported_partner(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    trip_id = _create_trip(api_client)
    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            **VALID_FLIGHT_SEARCH_REDIRECT,
            "preferred_partner": "redbus",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unsupported_partner"
    assert body["unsupported_partner"]["domain"] == "flight"
    assert body["unsupported_partner"]["requested_partner"] == "redbus"
    assert body["action"] is None


def test_bus_domain_allows_ixigo_and_redbus(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    for partner, expected_domain in (("ixigo", "www.ixigo.com"), ("redbus", "www.redbus.in")):
        response = api_client.post(
            f"/trips/{trip_id}/trusted-action",
            json={
                "action_type": "SEARCH_REDIRECT",
                "domain": "bus",
                "origin": "Kochi",
                "destination": "Alleppey",
                "trip_shape": "one_way",
                "departure_date": "2026-09-10",
                "traveler_count": 1,
                "preferred_partner": partner,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["action"]["target"]["target_url"].startswith(f"https://{expected_domain}/")


def test_stay_domain_resolves_each_allowlisted_partner(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    partner_domains = {
        "booking_com": "www.booking.com",
        "agoda": "www.agoda.com",
        "ixigo": "www.ixigo.com",
    }
    for partner, expected_domain in partner_domains.items():
        response = api_client.post(
            f"/trips/{trip_id}/trusted-action",
            json={
                "action_type": "SEARCH_REDIRECT",
                "domain": "stay",
                "destination": "Goa",
                "trip_shape": "one_way",
                "departure_date": "2026-09-10",
                "traveler_count": 2,
                "preferred_partner": partner,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["action"]["target"]["target_url"].startswith(f"https://{expected_domain}/")
        assert body["action"]["affiliate_disclosure"] is False
        assert body["action"]["capability"] is not None
        assert body["action"]["cta_label"] is not None


def test_ixigo_stay_resolves_to_native_hotel_destination_url(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "stay",
            "destination": "New Delhi",
            "preferred_partner": "ixigo",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    target = body["action"]["target"]
    assert target["partner"] == "ixigo"
    assert target["path"] == "hotels/hotels-in-new-delhi"
    assert target["query_params"] == {}
    assert target["target_url"] == "https://www.ixigo.com/hotels/hotels-in-new-delhi"
    assert body["action"]["capability"] == "destination_redirect"
    assert body["action"]["cta_label"] == "Browse ixigo hotels"


def test_booking_stay_resolves_to_confirmed_prefilled_search_url(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "stay",
            "destination": "Goa",
            "trip_shape": "round_trip",
            "departure_date": "2026-09-15",
            "return_date": "2026-09-16",
            "traveler_count": 2,
            "preferred_partner": "booking_com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    action = body["action"]
    assert action["affiliate_disclosure"] is False
    assert action["capability"] == "prefilled_search"
    assert action["cta_label"] == "Search Booking.com"
    target = action["target"]
    assert target["path"] == "searchresults.html"
    assert target["query_params"]["ss"] == "Goa"
    assert target["query_params"]["checkin"] == "2026-09-15"
    assert target["query_params"]["checkout"] == "2026-09-16"
    assert target["query_params"]["group_adults"] == "2"
    assert "marker" not in target["query_params"]


def test_agoda_unknown_destination_returns_disabled_not_generic_search(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "stay",
            "destination": "Coorg",
            "preferred_partner": "agoda",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disabled"
    assert body["action"] is None
    assert body["disabled"]["reason"] == "No confirmed useful provider redirect is available for this stay yet."


def test_stay_domain_resolves_without_origin_or_traveler_count(api_client: TestClient):
    # TWM-208: a stay/hotel search has no "origin" or per-leg traveler-count
    # concept the way a transport leg does, and every approved stay partner
    # already builds a valid search URL from destination alone. Before this
    # fix, a stay request with only destination+preferred_partner always
    # returned missing_input, permanently.
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "stay",
            "destination": "Coorg",
            "preferred_partner": "booking_com",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["action"]["target"]["target_url"].startswith("https://www.booking.com/searchresults.html?")


def test_stay_domain_still_requires_destination(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={"action_type": "SEARCH_REDIRECT", "domain": "stay", "preferred_partner": "hotellook"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "missing_input"
    assert body["missing_input"]["missing_fields"] == ["destination"]


def test_transport_domains_still_require_origin(api_client: TestClient):
    # Regression guard: TWM-208 narrows the origin requirement for domain
    # "stay" only — flight/train/bus must keep requiring origin exactly as
    # before. traveler_count is no longer required for any domain
    # (TWM-215).
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={"action_type": "SEARCH_REDIRECT", "domain": "train"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "missing_input"
    assert set(body["missing_input"]["missing_fields"]) == {"origin", "destination"}


def test_malformed_payload_with_extra_field_is_rejected_with_422(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={**VALID_FLIGHT_SEARCH_REDIRECT, "price": 500},
    )
    assert response.status_code == 422


def test_unknown_trip_returns_404(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    response = api_client.post(
        f"/trips/{uuid4()}/trusted-action",
        json=VALID_CHECK_PRICES,
    )
    assert response.status_code == 404


def test_guest_cannot_resolve_trusted_action_on_another_guests_trip():
    repository = MemoryTripRepository()
    _override_persistence(repository)
    app.dependency_overrides[get_current_user] = lambda: None
    with TestClient(app) as owner, TestClient(app) as stranger:
        trip_id = _create_trip(owner)
        response = stranger.post(f"/trips/{trip_id}/trusted-action", json=VALID_CHECK_PRICES)
        assert response.status_code == 404
    app.dependency_overrides.clear()


def test_read_query_boundary_never_mutates_trip_version(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)
    original_version = repository.trips[UUID(trip_id)].version

    api_client.post(f"/trips/{trip_id}/trusted-action", json=VALID_CHECK_PRICES)

    assert repository.trips[UUID(trip_id)].version == original_version


def test_ixigo_affiliate_id_is_appended_when_configured(api_client: TestClient):
    # TWM-196: flight's SEARCH_REDIRECT partner moved to Aviasales
    # (Travelpayouts-tracked, see test_travelpayouts_marker_is_appended_
    # when_configured below) — ixigo's own affiliate id is exercised here
    # via train, its remaining approved domain.
    repository = MemoryTripRepository()
    _override_persistence(repository)
    app.dependency_overrides[get_trusted_action_service] = lambda: TrustedActionService(
        logger=_logger(),
        settings=TrustedActionSettings(ixigo_affiliate_id="ek-999", travelpayouts_marker=None),
    )
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "train",
            "origin": "Delhi",
            "destination": "Mumbai",
            "trip_shape": "one_way",
            "departure_date": "2026-09-10",
            "traveler_count": 2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "affiliate_id=ek-999" in body["action"]["target"]["target_url"]


def test_travelpayouts_marker_is_appended_to_confirmed_travelpayouts_redirect_when_configured(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    app.dependency_overrides[get_trusted_action_service] = lambda: TrustedActionService(
        logger=_logger(),
        settings=TrustedActionSettings(ixigo_affiliate_id=None, travelpayouts_marker="marker-777"),
    )
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={
            "action_type": "SEARCH_REDIRECT",
            "domain": "flight",
            "origin": "Delhi",
            "destination": "Mumbai",
            "trip_shape": "one_way",
            "departure_date": "2026-09-10",
            "traveler_count": 2,
            "preferred_partner": "aviasales",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert "marker=marker-777" in body["action"]["target"]["target_url"]


def test_requested_events_are_logged_with_trip_id(api_client: TestClient):
    repository = MemoryTripRepository()
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        sink,
    )
    _override_persistence(repository)
    app.dependency_overrides[get_logger] = lambda: logger

    trip_id = _create_trip(api_client)
    api_client.post(f"/trips/{trip_id}/trusted-action", json=VALID_CHECK_PRICES)

    events = [event["event"] for event in sink.events]
    assert "be.trusted_action.requested" in events
    assert "be.trusted_action.resolved" in events
    resolved_event = next(e for e in sink.events if e["event"] == "be.trusted_action.resolved")
    assert resolved_event["fields"]["trip_id"] == trip_id


def test_readiness_rejection_is_logged(api_client: TestClient):
    repository = MemoryTripRepository()
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        sink,
    )
    _override_persistence(repository)
    app.dependency_overrides[get_logger] = lambda: logger

    trip_id = _create_trip(api_client)
    api_client.post(
        f"/trips/{trip_id}/trusted-action",
        json={"action_type": "SEARCH_REDIRECT", "domain": "bus"},
    )

    events = [event["event"] for event in sink.events]
    assert "be.trusted_action.readiness_rejected" in events


# --- Feasibility endpoint (TWM-195 root-fix: deterministic rules, no
# classifier of any kind) ----------------------------------------------------


def test_feasibility_endpoint_returns_route_valid_modes_for_a_medium_distance_route(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action/feasibility",
        json={"origin": "Bangalore", "destination": "Mangalore"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "excluded_modes" not in body
    modes = {mode["mode"]: mode for mode in body["modes"]}
    assert {"flight", "train", "bus"} <= set(modes)
    for mode in modes.values():
        assert mode["status"] == "feasible"
        assert mode["duration_source"] == "computed"
        assert mode["verification"]["status"] == "GENERAL_GUIDANCE"


def test_feasibility_endpoint_excludes_route_absurd_flight_for_a_short_hop(api_client: TestClient):
    # Bhubaneswar -> Puri: flight is route-absurd for this local hop.
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action/feasibility",
        json={"origin": "Bhubaneswar", "destination": "Puri"},
    )

    assert response.status_code == 200
    body = response.json()
    modes = {mode["mode"]: mode for mode in body["modes"]}
    assert "flight" not in modes
    assert modes["train"]["status"] == "feasible"
    assert modes["bus"]["status"] == "feasible"
    assert modes["drive"]["status"] == "feasible"


def test_feasibility_endpoint_returns_empty_modes_never_all_modes_for_unknown_cities(
    api_client: TestClient,
):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action/feasibility",
        json={"origin": "Some Remote Village", "destination": "Another Remote Village"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["modes"] == []


def test_feasibility_endpoint_returns_empty_modes_for_a_degenerate_route(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/trusted-action/feasibility",
        json={"origin": "Goa", "destination": "Goa"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body is not None
    assert body["modes"] == []


def test_feasibility_endpoint_unknown_trip_returns_404(api_client: TestClient):
    repository = MemoryTripRepository()
    _override_persistence(repository)

    response = api_client.post(
        f"/trips/{uuid4()}/trusted-action/feasibility",
        json={"origin": "Delhi", "destination": "Agra"},
    )
    assert response.status_code == 404


def test_feasibility_resolved_is_logged_with_returned_mode_names(api_client: TestClient):
    repository = MemoryTripRepository()
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        sink,
    )
    _override_persistence(repository)
    app.dependency_overrides[get_logger] = lambda: logger
    trip_id = _create_trip(api_client)

    api_client.post(
        f"/trips/{trip_id}/trusted-action/feasibility",
        json={"origin": "Bhubaneswar", "destination": "Puri"},
    )

    events = {event["event"]: event for event in sink.events}
    assert "be.trusted_action.feasibility.requested" in events
    resolved = events["be.trusted_action.feasibility.resolved"]
    assert set(resolved["fields"]["returned_modes"]) == {"train", "bus", "drive"}
    assert resolved["fields"]["returned_mode_count"] == 3
    app.dependency_overrides.pop(get_logger, None)


def test_feasibility_empty_outcome_is_logged_separately_from_resolved(api_client: TestClient):
    repository = MemoryTripRepository()
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        sink,
    )
    _override_persistence(repository)
    app.dependency_overrides[get_logger] = lambda: logger
    trip_id = _create_trip(api_client)

    api_client.post(
        f"/trips/{trip_id}/trusted-action/feasibility",
        json={"origin": "Some Remote Village", "destination": "Another Remote Village"},
    )

    events = [event["event"] for event in sink.events]
    assert "be.trusted_action.feasibility.empty" in events
    assert "be.trusted_action.feasibility.resolved" not in events
    app.dependency_overrides.pop(get_logger, None)
