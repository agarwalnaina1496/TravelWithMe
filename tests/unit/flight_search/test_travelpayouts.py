"""Travelpayouts Data API adapter tests (TWM-145)."""

import asyncio
from datetime import date

import httpx
import pytest

from twm.services.flight_search.errors import FlightProviderError, FlightProviderTimeoutError
from twm.services.flight_search.settings import FlightSearchSettings
from twm.services.flight_search.travelpayouts import TravelpayoutsAdapter


def _settings(**overrides) -> FlightSearchSettings:
    defaults = dict(
        api_token="test-token",
        partner_id=None,
        timeout_seconds=10,
        currency="USD",
    )
    defaults.update(overrides)
    return FlightSearchSettings(**defaults)


def test_adapter_sends_token_via_header_never_query_or_url() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "BOM": {
                        "0": {
                            "price": 5000,
                            "airline": "AI",
                            "flight_number": 101,
                            "departure_at": "2026-09-10T10:00:00+00:00",
                            "return_at": "2026-09-17T10:00:00+00:00",
                            "expires_at": "2026-08-20T00:00:00+00:00",
                        }
                    }
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        entries, received_at = asyncio.run(
            adapter.fetch_cheapest_prices(
                origin_iata="DEL",
                destination_iata="BOM",
                depart_date=date(2026, 9, 10),
                return_date=date(2026, 9, 17),
                currency="USD",
            )
        )
    finally:
        asyncio.run(client.aclose())

    request = captured[0]
    assert request.headers["X-Access-Token"] == "test-token"
    assert "test-token" not in str(request.url)
    assert len(entries) == 1
    assert entries[0]["price"] == 5000
    assert received_at is not None


def test_adapter_includes_optional_partner_id_as_marker() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"success": True, "data": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TravelpayoutsAdapter(_settings(partner_id="12345"), client)

    try:
        asyncio.run(
            adapter.fetch_cheapest_prices(
                origin_iata="DEL",
                destination_iata="BOM",
                depart_date=None,
                return_date=None,
                currency="USD",
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert captured[0].url.params["marker"] == "12345"


def test_adapter_returns_empty_list_when_destination_absent() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": True, "data": {}})
        )
    )
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        entries, _ = asyncio.run(
            adapter.fetch_cheapest_prices(
                origin_iata="DEL",
                destination_iata="BOM",
                depart_date=None,
                return_date=None,
                currency="USD",
            )
        )
    finally:
        asyncio.run(client.aclose())

    assert entries == []


def test_adapter_maps_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        with pytest.raises(FlightProviderTimeoutError) as captured:
            asyncio.run(
                adapter.fetch_cheapest_prices(
                    origin_iata="DEL",
                    destination_iata="BOM",
                    depart_date=None,
                    return_date=None,
                    currency="USD",
                )
            )
    finally:
        asyncio.run(client.aclose())

    error = captured.value
    assert error.component == "travelpayouts"
    assert error.failure_stage == "invocation"
    assert error.error_type == "ReadTimeout"


def test_adapter_maps_http_status_error_without_raw_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        with pytest.raises(FlightProviderError) as captured:
            asyncio.run(
                adapter.fetch_cheapest_prices(
                    origin_iata="DEL",
                    destination_iata="BOM",
                    depart_date=None,
                    return_date=None,
                    currency="USD",
                )
            )
    finally:
        asyncio.run(client.aclose())

    error = captured.value
    assert error.upstream_status_code == 401
    assert not hasattr(error, "upstream_response")


def test_adapter_maps_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("all connection attempts failed", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        with pytest.raises(FlightProviderError) as captured:
            asyncio.run(
                adapter.fetch_cheapest_prices(
                    origin_iata="DEL",
                    destination_iata="BOM",
                    depart_date=None,
                    return_date=None,
                    currency="USD",
                )
            )
    finally:
        asyncio.run(client.aclose())

    assert captured.value.failure_stage == "upstream_connection"


def test_adapter_maps_malformed_json() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not-json"))
    )
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        with pytest.raises(FlightProviderError) as captured:
            asyncio.run(
                adapter.fetch_cheapest_prices(
                    origin_iata="DEL",
                    destination_iata="BOM",
                    depart_date=None,
                    return_date=None,
                    currency="USD",
                )
            )
    finally:
        asyncio.run(client.aclose())

    assert captured.value.failure_stage == "response_decode"


def test_adapter_maps_success_false_response() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"success": False, "error": "bad request"})
        )
    )
    adapter = TravelpayoutsAdapter(_settings(), client)

    try:
        with pytest.raises(FlightProviderError) as captured:
            asyncio.run(
                adapter.fetch_cheapest_prices(
                    origin_iata="DEL",
                    destination_iata="BOM",
                    depart_date=None,
                    return_date=None,
                    currency="USD",
                )
            )
    finally:
        asyncio.run(client.aclose())

    assert captured.value.failure_stage == "response_contract"
