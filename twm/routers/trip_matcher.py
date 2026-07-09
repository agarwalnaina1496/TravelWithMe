from fastapi import APIRouter
from typing import Any, Dict
from ..core import get_engine
from ..schemas import MeridianRequest, MeridianResponse, ScoutRequest, ScoutResponse

router = APIRouter(tags=["Trip Matcher"])

engine = get_engine()


def _unwrap_agent_response(raw_response: Any) -> Dict[str, Any]:
    if isinstance(raw_response, list) and raw_response:
        raw_response = raw_response[0]

    if isinstance(raw_response, dict):
        if isinstance(raw_response.get("json"), dict):
            return raw_response["json"]
        if isinstance(raw_response.get("output"), dict):
            return raw_response["output"]
        return raw_response

    return {}


def _normalize_scout_response(raw_response: Any) -> ScoutResponse:
    response = _unwrap_agent_response(raw_response)
    return ScoutResponse(
        message=response.get("message") or "",
        state_delta=response.get("state_delta") or {},
        intent=response.get("intent"),
    )


def _normalize_meridian_response(raw_response: Any) -> MeridianResponse:
    response = _unwrap_agent_response(raw_response)
    return MeridianResponse(
        **{
            **response,
            "status": response.get("status") or "HARD_FAIL",
            "message": response.get("message") or "",
            "state_delta": response.get("state_delta") or {},
            "options": response.get("options") or [],
        }
    )


@router.post("/scout", response_model=ScoutResponse)
async def scout(payload: ScoutRequest):
    raw_response = engine.scout(payload.trip_state, payload.message)
    return _normalize_scout_response(raw_response)

@router.post("/meridian", response_model=MeridianResponse)
async def meridian(payload: MeridianRequest):
    raw_response = engine.meridian(payload.trip_state)
    return _normalize_meridian_response(raw_response)
