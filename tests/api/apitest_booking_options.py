"""API coverage for the batch booking-options endpoint (TWM-220).

``POST /trips/{id}/booking-options`` is a pure fan-out over the same
``TrustedActionService.resolve`` path the single-action endpoint uses — so
this file only asserts the fan-out behaviour (one request → many results,
one target's failure never fails the batch, the batch-boundary log, and that
per-resolve events still fire). Resolver correctness itself is covered by
``apitest_trusted_action.py``.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from twm.dependencies import get_logger, get_trip_persistence, get_trusted_action_service
from twm.main import app
from twm.persistence.contracts import GuestSession, TripRecord
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


def _service(repository):
    return TripPersistenceService(repository, DatabaseSettings(url=None, guest_cookie_secure=False))


def _create_trip(api_client: TestClient) -> str:
    created = api_client.post("/trips", json={"title": "Kerala", "trip_context": {"destination": "Test"}})
    assert created.status_code == 201
    return created.json()["id"]


def _capturing_service():
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        sink,
    )
    service = TrustedActionService(
        logger=logger,
        settings=TrustedActionSettings(ixigo_affiliate_id=None, travelpayouts_marker=None),
    )
    return service, sink


TRANSPORT_BODY = {
    "domain": "transport",
    "from_city": "Delhi",
    "to_city": "Mumbai",
    "departure_date": "2026-09-10",
    "party": {"adults": 2, "children": 1, "infants": 0},
    "targets": [
        {"kind": "mode", "value": "flight"},
        {"kind": "mode", "value": "train"},
        {"kind": "mode", "value": "bus"},
    ],
}


def test_transport_batch_resolves_every_target_in_one_request(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(f"/trips/{trip_id}/booking-options", json=TRANSPORT_BODY)

    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["target"]["value"] for r in results] == ["flight", "train", "bus"]
    assert all(r["status"] == "resolved" for r in results)
    assert all(r["action"]["action_type"] == "SEARCH_REDIRECT" for r in results)


def test_party_total_flows_through_as_the_resolved_traveler_count(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={**TRANSPORT_BODY, "targets": [{"kind": "mode", "value": "flight"}]},
    )

    assert response.status_code == 200
    [flight] = response.json()["results"]
    assert flight["action"]["traveler_count"] == 3
    assert "adults=3" in flight["action"]["target"]["target_url"]


def test_one_target_failing_does_not_fail_the_batch(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    # Agoda has no confirmed capability for an obscure destination (see
    # apitest_trusted_action.py) -> that one target is `disabled`; the other
    # two still resolve.
    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={
            "domain": "stay",
            "destination": "Coorg",
            "party": {"adults": 2, "children": 0, "infants": 0},
            "targets": [
                {"kind": "partner", "value": "booking_com"},
                {"kind": "partner", "value": "agoda"},
                {"kind": "partner", "value": "ixigo"},
            ],
        },
    )

    assert response.status_code == 200
    by_partner = {r["target"]["value"]: r["status"] for r in response.json()["results"]}
    assert by_partner == {"booking_com": "resolved", "agoda": "disabled", "ixigo": "resolved"}


def test_all_targets_can_fail_and_the_batch_still_returns_200(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={
            "domain": "transport",
            "party": {"adults": 1, "children": 0, "infants": 0},
            "targets": [{"kind": "mode", "value": "train"}, {"kind": "mode", "value": "bus"}],
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert all(r["status"] == "missing_input" for r in results)
    assert all(set(r["missing_input"]["missing_fields"]) == {"origin", "destination"} for r in results)


def test_unknown_target_value_is_a_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={
            "domain": "transport",
            "party": {"adults": 1, "children": 0, "infants": 0},
            "targets": [{"kind": "mode", "value": "boat"}],
        },
    )
    assert response.status_code == 422


def test_domain_and_target_kind_mismatch_is_a_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={
            "domain": "transport",
            "party": {"adults": 1, "children": 0, "infants": 0},
            "targets": [{"kind": "partner", "value": "booking_com"}],
        },
    )
    assert response.status_code == 422


def test_a_one_way_request_with_a_return_date_is_a_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={**TRANSPORT_BODY, "return_date": "2026-09-20",
              "targets": [{"kind": "mode", "value": "flight"}]},
    )
    assert response.status_code == 422


def test_a_return_date_before_departure_is_a_422(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={**TRANSPORT_BODY, "trip_shape": "round_trip",
              "departure_date": "2026-09-10", "return_date": "2026-09-05",
              "targets": [{"kind": "mode", "value": "flight"}]},
    )
    assert response.status_code == 422


def test_batch_boundary_and_per_resolve_events_are_both_emitted(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)
    service, sink = _capturing_service()
    app.dependency_overrides[get_trusted_action_service] = lambda: service
    trip_id = _create_trip(api_client)

    response = api_client.post(
        f"/trips/{trip_id}/booking-options",
        json={**TRANSPORT_BODY, "targets": [
            {"kind": "mode", "value": "flight"}, {"kind": "mode", "value": "train"},
        ]},
    )
    assert response.status_code == 200

    events = [e["event"] for e in sink.events]
    assert events.count("be.trusted_action.resolved") == 2
    [batch] = [e for e in sink.events if e["event"] == "be.trip.booking_options.batch"]
    assert batch["fields"]["domain"] == "transport"
    assert batch["fields"]["target_count"] == 2
    assert batch["fields"]["resolved_count"] == 2
    assert batch["fields"]["trip_id"] == trip_id


def test_missing_trip_is_a_404(api_client: TestClient):
    repository = MemoryTripRepository()
    app.dependency_overrides[get_trip_persistence] = lambda: _service(repository)

    response = api_client.post(
        f"/trips/{uuid4()}/booking-options",
        json={**TRANSPORT_BODY, "targets": [{"kind": "mode", "value": "flight"}]},
    )
    assert response.status_code == 404
