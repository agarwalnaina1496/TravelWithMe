from contextlib import AsyncExitStack, asynccontextmanager
import logging

import httpx
import uvicorn
from fastapi import FastAPI
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


logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def application_lifespan(app: FastAPI):
    settings = AgentEngineSettings.load()
    async with AsyncExitStack() as stack:
        http_client = None
        if settings.engine == "n8n":
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=60.0)
            )
        app.state.agent_engine = get_agent_engine(settings, http_client)
        yield


def initialize_app() -> FastAPI:
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
        allow_headers=["Content-Type"],
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

    @app.exception_handler(AgentOutputError)
    async def handle_invalid_agent_output(_, error: AgentOutputError):
        logger.warning(
            "%s output failed the common contract: %s",
            error.agent,
            error.failures,
        )
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant returned an invalid response."},
        )

    @app.exception_handler(AgentAdapterTimeoutError)
    async def handle_agent_timeout(_, error: AgentAdapterTimeoutError):
        logger.warning("Agent invocation timed out: %s", type(error).__name__)
        return JSONResponse(
            status_code=504,
            content={"detail": "The travel assistant timed out."},
        )

    @app.exception_handler(AgentAdapterError)
    async def handle_agent_adapter_error(_, error: AgentAdapterError):
        logger.warning("Agent invocation failed: %s", type(error).__name__)
        return JSONResponse(
            status_code=502,
            content={"detail": "The travel assistant is temporarily unavailable."},
        )

    @app.exception_handler(ValidationError)
    async def handle_unexpected_agent_validation(_, error: ValidationError):
        failure_types = [
            item["type"] for item in error.errors(include_input=False)
        ]
        logger.warning(
            "Agent response normalization failed: types=%s", failure_types
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
