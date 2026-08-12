import asyncio

import httpx
from fastapi import FastAPI

from twm.telemetry import (
    InMemorySink,
    PayloadMode,
    TelemetryContextMiddleware,
    TelemetryLogger,
    TelemetrySettings,
    get_correlation_context,
)


def telemetry_app(sink, request_id_factory=lambda: "generated-request") -> FastAPI:
    app = FastAPI()
    logger = TelemetryLogger(
        TelemetrySettings(True, "test", PayloadMode.METADATA, 1024), sink
    )
    app.add_middleware(
        TelemetryContextMiddleware,
        logger=logger,
        request_id_factory=request_id_factory,
    )

    @app.post("/scout")
    async def scout() -> dict:
        context = get_correlation_context()
        await asyncio.sleep(0.01)
        return {
            "request_id": context.request_id,
            "trip_id": context.trip_id,
            "turn_id": context.turn_id,
        }

    @app.post("/meridian")
    async def meridian() -> None:
        raise RuntimeError("unexpected route failure")

    @app.post("/guide")
    async def guide() -> dict:
        context = get_correlation_context()
        return {
            "request_id": context.request_id,
            "trip_id": context.trip_id,
            "turn_id": context.turn_id,
        }

    @app.get("/trips/{trip_id}")
    async def get_trip(trip_id: str) -> dict:
        context = get_correlation_context()
        return {"request_id": context.request_id}

    @app.get("/health")
    async def health() -> dict:
        return {"correlated": get_correlation_context() is not None}

    return app


def test_middleware_generates_request_id_and_propagates_valid_optional_ids() -> None:
    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(InMemorySink()))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/scout",
                headers={"X-TWM-Trip-ID": "trip-1", "X-TWM-Turn-ID": "turn-1"},
            )

        assert response.status_code == 200
        assert response.headers["X-TWM-Request-ID"] == "generated-request"
        assert response.headers["X-TWM-Trip-ID"] == "trip-1"
        assert response.headers["X-TWM-Turn-ID"] == "turn-1"
        assert response.json() == {
            "request_id": "generated-request",
            "trip_id": "trip-1",
            "turn_id": "turn-1",
        }

    asyncio.run(exercise())


def test_invalid_optional_ids_remain_unset_and_invalid_request_id_is_replaced() -> None:
    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(InMemorySink()))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/scout",
                headers={
                    "X-TWM-Request-ID": "invalid request",
                    "X-TWM-Trip-ID": "also invalid",
                    "X-TWM-Turn-ID": "turn-2",
                },
            )

        assert response.headers["X-TWM-Request-ID"] != "invalid request"
        assert "X-TWM-Trip-ID" not in response.headers
        assert response.headers["X-TWM-Turn-ID"] == "turn-2"

    asyncio.run(exercise())


def test_concurrent_requests_do_not_leak_context() -> None:
    sink = InMemorySink()

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(sink))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            first, second = await asyncio.gather(
                client.post(
                    "/scout",
                    headers={"X-TWM-Request-ID": "request-a", "X-TWM-Trip-ID": "trip-a"},
                ),
                client.post(
                    "/scout",
                    headers={"X-TWM-Request-ID": "request-b", "X-TWM-Trip-ID": "trip-b"},
                ),
            )
        assert first.json()["trip_id"] == "trip-a"
        assert second.json()["trip_id"] == "trip-b"

    asyncio.run(exercise())
    assert sink.events == []
    assert get_correlation_context() is None


def test_middleware_logs_only_unexpected_request_failures() -> None:
    sink = InMemorySink()

    async def exercise() -> None:
        transport = httpx.ASGITransport(
            app=telemetry_app(sink), raise_app_exceptions=False
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/meridian",
                headers={"X-TWM-Request-ID": "request-failure"},
            )
        assert response.status_code == 500

    asyncio.run(exercise())
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event"] == "be.http.request.failed"
    assert event["request_id"] == "request-failure"
    assert event["fields"]["component"] == "fastapi"


def test_correlation_applies_beyond_scout_and_meridian() -> None:
    """Guide/Atlas/trip-command routes must get the same correlation context
    Scout and Meridian already do — this used to be a hardcoded allowlist
    that silently excluded every route added after TWM-71."""

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(InMemorySink()))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/guide",
                headers={"X-TWM-Trip-ID": "trip-1", "X-TWM-Turn-ID": "turn-1"},
            )

        assert response.status_code == 200
        assert response.headers["X-TWM-Request-ID"] == "generated-request"
        assert response.headers["X-TWM-Trip-ID"] == "trip-1"
        assert response.json() == {
            "request_id": "generated-request",
            "trip_id": "trip-1",
            "turn_id": "turn-1",
        }

    asyncio.run(exercise())


def test_correlation_applies_to_get_requests_too() -> None:
    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(InMemorySink()))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/trips/abc")

        assert response.status_code == 200
        assert response.headers["X-TWM-Request-ID"] == "generated-request"
        assert response.json() == {"request_id": "generated-request"}

    asyncio.run(exercise())


def test_health_checks_are_not_correlated() -> None:
    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(InMemorySink()))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/health")

        assert response.status_code == 200
        assert "X-TWM-Request-ID" not in response.headers
        assert response.json() == {"correlated": False}

    asyncio.run(exercise())


def test_broken_sink_does_not_change_api_response() -> None:
    class BrokenSink:
        def emit(self, event):
            raise RuntimeError("broken")

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=telemetry_app(BrokenSink()))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post("/scout")
        assert response.status_code == 200
        assert response.json()["request_id"] == "generated-request"

    asyncio.run(exercise())
