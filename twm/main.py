from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from .shared.properties import property_loader
from .middleware import SecurityBoundaryMiddleware
from .routers.health import router as health_api
from .routers.trip_matcher import router as trip_matcher_api
from .services import (
    AgentAdapterError,
    AgentAdapterTimeoutError,
    AgentEngineSettings,
    AgentOutputError,
    get_agent_engine,
)
from .telemetry import (
    CORRELATION_HEADERS,
    TelemetryContextMiddleware,
    TelemetryLogger,
    TelemetrySettings,
    build_telemetry_sink,
)

@asynccontextmanager
async def application_lifespan(app: FastAPI):
    settings = AgentEngineSettings.load()
    async with AsyncExitStack() as stack:
        http_client = None
        if settings.engine == "n8n":
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=60.0)
            )
        app.state.agent_engine = get_agent_engine(
            settings, app.state.telemetry, http_client
        )
        try:
            yield
        finally:
            app.state.telemetry.shutdown()


def initialize_app() -> FastAPI:
    telemetry_settings = TelemetrySettings.load()
    telemetry_logger = TelemetryLogger(
        settings=telemetry_settings,
        sink=build_telemetry_sink(
            environment=telemetry_settings.environment,
            service=telemetry_settings.service,
        ),
    )
    app = FastAPI(
        title="TravelWithMe Trip Matcher",
        lifespan=application_lifespan,
        docs_url=None if property_loader.get_environment() == "prod" else "/docs",
        redoc_url=None if property_loader.get_environment() == "prod" else "/redoc",
        openapi_url=None if property_loader.get_environment() == "prod" else "/openapi.json",
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=property_loader.get_list_property("trusted_hosts"),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=property_loader.get_list_property("cors_allowed_origins"),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", *CORRELATION_HEADERS],
        expose_headers=list(CORRELATION_HEADERS),
    )
    app.add_middleware(
        SecurityBoundaryMiddleware,
        requests_per_minute=property_loader.get_int_property_with_default(
            "requests_per_minute", 60
        ),
        max_body_bytes=property_loader.get_int_property_with_default(
            "max_request_body_bytes", 131_072
        ),
    )
    app.add_middleware(TelemetryContextMiddleware, logger=telemetry_logger)
    app.state.telemetry = telemetry_logger

    @app.exception_handler(AgentOutputError)
    async def handle_invalid_agent_output(request: Request, error: AgentOutputError):
        logger = request.app.state.telemetry
        logger.error(
            f"{error.agent.capitalize()} request failed after repair",
            event="be.http.request.failed",
            source="http",
            agent=error.agent,
            status_code=502,
            error_type=type(error).__name__,
            validation_failures=error.failures,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant returned an invalid response."},
        )

    @app.exception_handler(AgentAdapterTimeoutError)
    async def handle_agent_timeout(request: Request, error: AgentAdapterTimeoutError):
        logger = request.app.state.telemetry
        logger.error(
            "Agent request timed out",
            event="be.http.request.failed",
            source="http",
            status_code=504,
            error_type=type(error).__name__,
        )
        return JSONResponse(
            status_code=504,
            content={"detail": "The travel assistant timed out."},
        )

    @app.exception_handler(AgentAdapterError)
    async def handle_agent_adapter_error(request: Request, error: AgentAdapterError):
        logger = request.app.state.telemetry
        logger.error(
            "Agent request failed",
            event="be.http.request.failed",
            source="http",
            status_code=502,
            error_type=type(error).__name__,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant is temporarily unavailable."},
        )

    @app.exception_handler(ValidationError)
    async def handle_unexpected_agent_validation(
        request: Request, error: ValidationError
    ):
        failure_types = [
            item["type"] for item in error.errors(include_input=False)
        ]
        logger = request.app.state.telemetry
        logger.error(
            "Agent response normalization failed",
            event="be.response.normalization_failed",
            source="http",
            status_code=502,
            validation_failure_types=failure_types,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant returned an invalid response."},
        )

    app.include_router(health_api)
    app.include_router(trip_matcher_api)

    return app


# Expose the FastAPI application at import time so ASGI servers can load it
app = initialize_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
