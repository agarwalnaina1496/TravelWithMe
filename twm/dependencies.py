from fastapi import Depends, HTTPException, Request
from typing import Annotated

from .auth.service import AuthService
from .persistence.contracts import User
from .services import AgentEngine
from .telemetry import TelemetryLogger
from .persistence.service import TripPersistenceService


def get_engine(request: Request) -> AgentEngine:
    return request.app.state.agent_engine


def get_logger(request: Request) -> TelemetryLogger:
    return request.app.state.telemetry


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
