import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .shared.properties import property_loader
from .routers.health import router as health_api
from .routers.trip_matcher import router as trip_matcher_api


def initialize_app() -> FastAPI:
    app = FastAPI(title="TravelWithMe Trip Matcher")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=property_loader.get_list_property("cors_allowed_origins"),
        allow_origin_regex=property_loader.get_string_property_with_default("cors_allowed_origin_regex", ""),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health_api)
    app.include_router(trip_matcher_api)

    return app


# Expose the FastAPI application at import time so ASGI servers can load it
app = initialize_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
