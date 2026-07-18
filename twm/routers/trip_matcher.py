from typing import Annotated

from fastapi import APIRouter, Depends

from ..core import get_engine
from ..schemas import MeridianRequest, MeridianResponse, ScoutRequest, ScoutResponse
from ..services import AgentEngine
from ..services.response_normalization import (
    _normalize_meridian_response,
    _normalize_scout_response,
)

router = APIRouter(tags=["Trip Matcher"])

EngineDependency = Annotated[AgentEngine, Depends(get_engine)]


@router.post("/scout", response_model=ScoutResponse)
async def scout(payload: ScoutRequest, engine: EngineDependency):
    execution = await engine.scout(payload.trip_state.model_dump(), payload.message)
    return _normalize_scout_response(execution)


@router.post(
    "/meridian", response_model=MeridianResponse, response_model_exclude_none=True
)
async def meridian(payload: MeridianRequest, engine: EngineDependency):
    execution = await engine.meridian(payload.trip_state.model_dump(), payload.message)
    return _normalize_meridian_response(execution)
