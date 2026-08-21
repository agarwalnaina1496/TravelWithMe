"""Stateless Trip Planner agent execution routes."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..dependencies import get_engine, get_logger
from ..schemas import AtlasRequest, AtlasResponse, GuideRequest, GuideResponse
from ..services import AgentEngine
from ..services.response_normalization import (
    _normalize_atlas_response,
    _normalize_guide_response,
)
from ..telemetry import TelemetryLogger


router = APIRouter(tags=["Trip Planner"])

EngineDependency = Annotated[AgentEngine, Depends(get_engine)]
LoggerDependency = Annotated[TelemetryLogger, Depends(get_logger)]


@router.post("/guide", response_model=GuideResponse)
async def guide(
    payload: GuideRequest,
    engine: EngineDependency,
    logger: LoggerDependency,
):
    request_data = payload.model_dump(mode="json", exclude_none=True)
    logger.info(
        "Received Guide request. Request - "
        f"{logger.format_json(request_data)}",
        event="be.request.validated",
        source="http",
        agent="guide",
        payload=request_data,
    )
    # No guide_event field on the agent payload — every MESSAGE turn is
    # handled identically (see guide.md); APPROVE_PLAN is never forwarded
    # to Guide in the real trip-command flow (planner_commands.py), and
    # this stateless debug route mirrors that by not exposing it either.
    agent_state = payload.trip_state.model_dump(mode="json")
    execution = await engine.guide(agent_state, payload.message)
    response = _normalize_guide_response(execution)
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        "Returning Guide response. Response - "
        f"{logger.format_json(response_data)}",
        event="be.response.normalized",
        source="http",
        agent="guide",
        status="success",
        response=response_data,
    )
    return response


@router.post("/atlas", response_model=AtlasResponse)
async def atlas(
    payload: AtlasRequest,
    engine: EngineDependency,
    logger: LoggerDependency,
):
    request_data = payload.model_dump(mode="json", exclude_none=True)
    logger.info(
        "Received Atlas request. Request - "
        f"{logger.format_json(request_data)}",
        event="be.request.validated",
        source="http",
        agent="atlas",
        payload=request_data,
    )
    execution = await engine.atlas(request_data, None)
    response = _normalize_atlas_response(execution)
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        "Returning Atlas response. Response - "
        f"{logger.format_json(response_data)}",
        event="be.response.normalized",
        source="http",
        agent="atlas",
        status="success",
        response=response_data,
    )
    return response
