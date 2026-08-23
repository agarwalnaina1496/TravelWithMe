from fastapi import Depends, HTTPException, Request
from typing import Annotated

from .auth.service import AuthService
from .persistence.contracts import User
from .services import AgentEngine
from .services.flight_search import FlightSearchService
from .services.trusted_action import TrustedActionService
from .telemetry import TelemetryLogger
from .persistence.service import TripPersistenceService


def get_engine(request: Request) -> AgentEngine:
    return request.app.state.agent_engine


def get_logger(request: Request) -> TelemetryLogger:
    return request.app.state.telemetry


def get_flight_search_service(
    request: Request, logger: Annotated[TelemetryLogger, Depends(get_logger)]
) -> FlightSearchService:
    return FlightSearchService(
        logger=logger,
        adapter=request.app.state.flight_search_adapter,
        currency=request.app.state.flight_search_settings.currency,
    )


def get_trusted_action_service(
    request: Request, logger: Annotated[TelemetryLogger, Depends(get_logger)]
) -> TrustedActionService:
    return TrustedActionService(
        logger=logger,
        settings=request.app.state.trusted_action_settings,
        route_classifier=request.app.state.route_classifier,
    )


def get_trip_persistence(request: Request) -> TripPersistenceService:
    persistence = request.app.state.trip_persistence
    if persistence is None:
        raise HTTPException(status_code=503, detail="Trip persistence is unavailable.")
    return persistence


def get_auth_service(request: Request) -> AuthService:
    auth = request.app.state.auth_service
    if auth is None:
        raise HTTPException(status_code=503, detail="Account authentication is unavailable.")
    return auth


async def get_current_user(request: Request, auth: Annotated[AuthService, Depends(get_auth_service)]) -> User | None:
    """The authenticated user for this request, or None if unauthenticated."""
    return await auth.current_user(request)
