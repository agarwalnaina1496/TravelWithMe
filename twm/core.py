from fastapi import HTTPException, Request

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
