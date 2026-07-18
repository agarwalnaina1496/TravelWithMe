import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from .shared.properties import property_loader
from .middleware import SecurityBoundaryMiddleware
from .routers.health import router as health_api
from .routers.trip_matcher import router as trip_matcher_api


def initialize_app() -> FastAPI:
    app = FastAPI(
        title="TravelWithMe Trip Matcher",
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
    app.include_router(health_api)
    app.include_router(trip_matcher_api)

    return app


# Expose the FastAPI application at import time so ASGI servers can load it
app = initialize_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
