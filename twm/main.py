from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
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
                httpx.AsyncClient(timeout=float(settings.n8n_timeout_seconds))
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
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant returned an invalid response."},
        )

    @app.exception_handler(AgentAdapterTimeoutError)
    async def handle_agent_timeout(request: Request, error: AgentAdapterTimeoutError):
        return JSONResponse(
            status_code=504,
            content={"detail": "The travel assistant timed out."},
        )

    @app.exception_handler(AgentAdapterError)
    async def handle_agent_adapter_error(request: Request, error: AgentAdapterError):
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
            "FastAPI failed to normalize agent response. Detail - "
            f"ResponseValidationError: {len(failure_types)} contract violation(s).",
            event="be.response.normalization_failed",
            source="http",
            component="fastapi",
            operation="agent.response.normalize",
            failure_stage="response_normalization",
            status_code=502,
            error_type="ResponseValidationError",
            validation_failure_types=failure_types,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant returned an invalid response."},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request, error: RequestValidationError
    ):
        path = request.scope.get("path", "")
        agent = path.removeprefix("/") if path in {"/scout", "/meridian"} else "agent"
        failure_types = [
            item["type"] for item in error.errors()
        ]
        request.app.state.telemetry.warning(
            f"FastAPI rejected {agent.capitalize()} request. Detail - "
            f"RequestValidationError: {len(failure_types)} validation failure(s).",
            event="be.http.request.validation_failed",
            source="http",
            component="fastapi",
            operation=f"{agent}.request.validate",
            failure_stage="request_validation",
            error_type="RequestValidationError",
            status_code=422,
            validation_failure_types=failure_types,
        )
        return await request_validation_exception_handler(request, error)

    app.include_router(health_api)
    app.include_router(trip_matcher_api)

    return app


# Expose the FastAPI application at import time so ASGI servers can load it
app = initialize_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
