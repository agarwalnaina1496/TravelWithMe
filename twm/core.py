from fastapi import Request

from .services import AgentEngine
from .telemetry import TelemetryLogger


def get_engine(request: Request) -> AgentEngine:
    return request.app.state.agent_engine


def get_logger(request: Request) -> TelemetryLogger:
    return request.app.state.telemetry
