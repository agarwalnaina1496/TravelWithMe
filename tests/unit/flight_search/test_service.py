"""FlightSearchService provider-integration tests (TWM-145).

Mocks AviasalesAdapter directly (no real network calls) to cover the
provider-configured and provider-not-configured paths, plus typed failure
mapping and filter/dedupe behavior.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from twm.schemas.flight_search import FlightSearchBudgetCeiling, FlightSearchRequest, FlightSearchTravelerCount
from twm.services.flight_search.errors import FlightProviderError, FlightProviderTimeoutError
from twm.services.flight_search.service import FlightSearchService
from twm.telemetry import InMemorySink, PayloadMode, TelemetryLogger, TelemetrySettings

RECEIVED_AT = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _logger() -> tuple[TelemetryLogger, InMemorySink]:
    sink = InMemorySink()
    logger = TelemetryLogger(
        TelemetrySettings(
            enabled=True, environment="test", payload_mode=PayloadMode.METADATA, max_field_size=256
        ),
        sink,
    )
    return logger, sink


def _ready_request(**overrides) -> FlightSearchRequest:
    defaults = dict(
        origin_iata="DEL",
        destination_iata="BOM",
        trip_type="round_trip",
        departure_date="2026-09-10",
        return_date="2026-09-17",
        travelers=FlightSearchTravelerCount(adults=2, children=0, infants=0),
    )
    defaults.update(overrides)
    return FlightSearchRequest(**defaults)


def _entry(**overrides) -> dict:
    defaults = dict(
        price=5000,
        airline="AI",
        flight_number=101,
        departure_at="2026-09-10T10:00:00+00:00",
        return_at="2026-09-17T10:00:00+00:00",
        expires_at="2026-08-20T00:00:00+00:00",
    )
    defaults.update(overrides)
    return defaults


class _FakeAdapter:
    def __init__(self, entries=None, error=None, stop_count_hint=None):
        self._entries = entries or []
        self._error = error
        self._stop_count_hint = stop_count_hint

    async def fetch_cheapest_prices(self, **kwargs):
        if self._error:
            raise self._error
        return self._entries, RECEIVED_AT

    async def fetch_stop_count_hint(self, **kwargs):
        return self._stop_count_hint


def test_no_adapter_configured_returns_provider_not_configured():
    logger, _ = _logger()
    service = FlightSearchService(logger=logger, adapter=None)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_not_configured"


def test_configured_adapter_returns_normalized_offer():
    logger, sink = _logger()
    adapter = _FakeAdapter(entries=[_entry()])
    service = FlightSearchService(logger=logger, adapter=adapter, currency="USD")

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "offer"
    assert len(response.offers) == 1
    assert response.offers[0].money.currency == "USD"
    events = [event["event"] for event in sink.events]
    assert "be.flight_search.provider_call_started" in events
    assert "be.flight_search.provider_call_completed" in events


def test_timeout_maps_to_provider_timeout_unavailable():
    logger, sink = _logger()
    adapter = _FakeAdapter(error=FlightProviderTimeoutError("slow"))
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_timeout"
    events = [event["event"] for event in sink.events]
    assert "be.flight_search.provider_call_failed" in events


def test_401_maps_to_provider_unauthorized():
    logger, _ = _logger()
    adapter = _FakeAdapter(error=FlightProviderError("bad token", upstream_status_code=401))
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_unauthorized"


def test_generic_provider_error_maps_to_provider_rejected_request():
    logger, _ = _logger()
    adapter = _FakeAdapter(error=FlightProviderError("boom", upstream_status_code=500))
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_rejected_request"


def test_empty_provider_result_maps_to_provider_rejected_request():
    logger, _ = _logger()
    adapter = _FakeAdapter(entries=[])
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_rejected_request"


def test_max_stops_has_nothing_to_filter_without_calendar_enrichment():
    # /v1/prices/cheap alone never discloses stop_count; without a
    # calendar-enrichment hint, max_stops filtering is a no-op passthrough.
    logger, _ = _logger()
    adapter = _FakeAdapter(entries=[_entry()])
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request(max_stops=0)))

    assert response.status == "offer"
    assert len(response.offers) == 1
    assert response.offers[0].stop_count is None


def test_calendar_hint_enriches_matching_offers_stop_count():
    logger, sink = _logger()
    adapter = _FakeAdapter(
        entries=[_entry()], stop_count_hint=("AI", "101", 0)
    )
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.offers[0].stop_count == 0
    events = {event["event"]: event for event in sink.events}
    assert events["be.flight_search.provider_call_completed"]["fields"]["stop_count_enriched"] is True


def test_calendar_hint_that_matches_nothing_leaves_stop_count_none():
    logger, sink = _logger()
    adapter = _FakeAdapter(
        entries=[_entry()], stop_count_hint=("UK", "999", 1)
    )
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.offers[0].stop_count is None
    events = {event["event"]: event for event in sink.events}
    assert events["be.flight_search.provider_call_completed"]["fields"]["stop_count_enriched"] is False


def test_max_stops_filters_out_offer_enriched_above_the_limit():
    logger, _ = _logger()
    adapter = _FakeAdapter(entries=[_entry()], stop_count_hint=("AI", "101", 2))
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request(max_stops=0)))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_rejected_request"


def test_budget_ceiling_filters_out_offer_over_budget():
    logger, _ = _logger()
    adapter = _FakeAdapter(entries=[_entry(price=50000)])
    service = FlightSearchService(logger=logger, adapter=adapter, currency="USD")

    request = _ready_request(
        budget_ceiling=FlightSearchBudgetCeiling(currency="USD", group_total_minor_units_max=1_000_000)
    )
    response = asyncio.run(service.search(uuid4(), request))

    assert response.status == "unavailable"
    assert response.unavailable.code == "provider_rejected_request"


def test_duplicate_entries_are_deduped_to_partial():
    logger, _ = _logger()
    adapter = _FakeAdapter(entries=[_entry(), _entry()])
    service = FlightSearchService(logger=logger, adapter=adapter)

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "partial"
    assert len(response.offers) == 1


def test_offers_are_ranked_cheapest_first_and_top_offer_is_recommended():
    logger, _ = _logger()
    adapter = _FakeAdapter(
        entries=[
            _entry(price=9000, flight_number=201),
            _entry(price=3000, flight_number=202),
            _entry(price=6000, flight_number=203),
        ]
    )
    service = FlightSearchService(logger=logger, adapter=adapter, currency="USD")

    response = asyncio.run(service.search(uuid4(), _ready_request()))

    assert response.status == "offer"
    group_totals = [offer.money.group_total_minor_units for offer in response.offers]
    assert group_totals == sorted(group_totals)
    assert response.offers[0].is_recommended is True
    assert all(offer.is_recommended is False for offer in response.offers[1:])


def test_clarification_needed_never_calls_provider():
    logger, sink = _logger()

    class ExplodingAdapter:
        async def fetch_cheapest_prices(self, **kwargs):
            raise AssertionError("provider must not be called when clarification is needed")

    service = FlightSearchService(logger=logger, adapter=ExplodingAdapter())
    response = asyncio.run(
        service.search(uuid4(), FlightSearchRequest(travelers=FlightSearchTravelerCount(adults=1)))
    )

    assert response.status == "clarification_needed"
