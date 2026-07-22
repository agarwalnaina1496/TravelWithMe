from typing import Annotated

from fastapi import APIRouter, Depends

from ..core import get_engine, get_telemetry
from ..schemas import MeridianRequest, MeridianResponse, ScoutRequest, ScoutResponse
from ..services import AgentEngine
from ..services.response_normalization import (
    _normalize_meridian_response,
    _normalize_scout_response,
)
from ..telemetry import TelemetryLogger

router = APIRouter(tags=["Trip Matcher"])

EngineDependency = Annotated[AgentEngine, Depends(get_engine)]
TelemetryDependency = Annotated[TelemetryLogger, Depends(get_telemetry)]


@router.post("/scout", response_model=ScoutResponse)
async def scout(
    payload: ScoutRequest,
    engine: EngineDependency,
    telemetry: TelemetryDependency,
):
    request_data = payload.model_dump(mode="json", exclude_none=True)
    telemetry.info(
        "Received Scout request",
        event="be.request.validated",
        source="http",
        agent="scout",
        payload=request_data,
    )
    execution = await engine.scout(payload.trip_state.model_dump(), payload.message)
    response = _normalize_scout_response(execution)
    telemetry.info(
        "Returning Scout response",
        event="be.response.normalized",
        source="http",
        agent="scout",
        status="success",
        response=response.model_dump(mode="json", exclude_none=True),
    )
    return response


@router.post(
    "/meridian", response_model=MeridianResponse, response_model_exclude_none=True
)
async def meridian(
    payload: MeridianRequest,
    engine: EngineDependency,
    telemetry: TelemetryDependency,
):
    request_data = payload.model_dump(mode="json", exclude_none=True)
    telemetry.info(
        "Received Meridian request",
        event="be.request.validated",
        source="http",
        agent="meridian",
        payload=request_data,
    )
    execution = await engine.meridian(payload.trip_state.model_dump(), payload.message)
    response = _normalize_meridian_response(execution)
    telemetry.info(
        "Returning Meridian response",
        event="be.response.normalized",
        source="http",
        agent="meridian",
        status="success",
        response=response.model_dump(mode="json", exclude_none=True),
    )
    return response
