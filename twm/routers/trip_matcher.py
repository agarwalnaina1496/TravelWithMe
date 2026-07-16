from fastapi import APIRouter

from ..core import get_engine
from ..schemas import MeridianRequest, MeridianResponse, ScoutRequest, ScoutResponse
from ..services.response_normalization import (
    _normalize_meridian_response,
    _normalize_scout_response,
)

router = APIRouter(tags=["Trip Matcher"])

engine = get_engine()


@router.post("/scout", response_model=ScoutResponse)
async def scout(payload: ScoutRequest):
    execution = engine.scout(payload.trip_state, payload.message)
    return _normalize_scout_response(execution)


@router.post(
    "/meridian", response_model=MeridianResponse, response_model_exclude_none=True
)
async def meridian(payload: MeridianRequest):
    execution = engine.meridian(payload.trip_state.model_dump(), payload.message)
    return _normalize_meridian_response(execution)
