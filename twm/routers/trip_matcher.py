from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..core import get_engine
from ..schemas import MeridianRequest, MeridianResponse, ScoutRequest, ScoutResponse
from ..services import AgentEngine
from ..services.response_normalization import (
    _normalize_meridian_response,
    _normalize_scout_response,
)
from ..telemetry import TelemetryLogger

router = APIRouter(tags=["Trip Matcher"])

EngineDependency = Annotated[AgentEngine, Depends(get_engine)]


@router.post("/scout", response_model=ScoutResponse)
async def scout(
    payload: ScoutRequest,
    request: Request,
    engine: EngineDependency,
):
    telemetry = request.app.state.telemetry
    if isinstance(telemetry, TelemetryLogger):
        telemetry.event(
            "be.request.validated",
            source="http",
            fields={"method": "POST", "path": "/scout"},
            payload=payload.model_dump(mode="json", exclude_none=True),
        )
    execution = await engine.scout(payload.trip_state.model_dump(), payload.message)
    response = _normalize_scout_response(execution)
    if isinstance(telemetry, TelemetryLogger):
        telemetry.event(
            "be.response.normalized",
            source="http",
            fields={"method": "POST", "path": "/scout"},
            payload=response.model_dump(mode="json", exclude_none=True),
        )
    return response


@router.post(
    "/meridian", response_model=MeridianResponse, response_model_exclude_none=True
)
async def meridian(
    payload: MeridianRequest,
    request: Request,
    engine: EngineDependency,
):
    telemetry = request.app.state.telemetry
    if isinstance(telemetry, TelemetryLogger):
        telemetry.event(
            "be.request.validated",
            source="http",
            fields={"method": "POST", "path": "/meridian"},
            payload=payload.model_dump(mode="json", exclude_none=True),
        )
    execution = await engine.meridian(payload.trip_state.model_dump(), payload.message)
    response = _normalize_meridian_response(execution)
    if isinstance(telemetry, TelemetryLogger):
        telemetry.event(
            "be.response.normalized",
            source="http",
            fields={"method": "POST", "path": "/meridian"},
            payload=response.model_dump(mode="json", exclude_none=True),
        )
    return response
