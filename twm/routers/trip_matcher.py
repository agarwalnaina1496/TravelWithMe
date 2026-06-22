from fastapi import APIRouter
from ..core import get_engine
from ..schemas import MeridianRequest, ScoutRequest

router = APIRouter(tags=["Trip Matcher"])

engine = get_engine()

@router.post("/scout")
async def scout(payload: ScoutRequest):
    return engine.scout(payload.trip_state, payload.message)

@router.post("/meridian")
async def meridian(payload: MeridianRequest):
    return engine.meridian(payload.trip_context)
