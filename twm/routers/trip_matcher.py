from typing import Any, Dict

from fastapi import APIRouter

from ..core import get_engine
from ..schemas import (
    AgentMeta,
    MeridianRequest,
    MeridianResponse,
    ScoutRequest,
    ScoutResponse,
)
from ..services import AgentExecution

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


def _agent_meta(execution: AgentExecution) -> AgentMeta:
    release = execution.prompt_release
    return AgentMeta(agent=release.agent, prompt_version=release.version)


def _normalize_scout_response(execution: AgentExecution) -> ScoutResponse:
    response = _unwrap_agent_response(execution.response)
    return ScoutResponse(
        message=response.get("message") or "",
        state_delta=response.get("state_delta") or {},
        intent=response.get("intent"),
        agent_meta=_agent_meta(execution),
    )


def _normalize_meridian_response(execution: AgentExecution) -> MeridianResponse:
    response = _unwrap_agent_response(execution.response)
    return MeridianResponse(
        **{
            **response,
            "status": response.get("status") or "HARD_FAIL",
            "message": response.get("message") or "",
            "state_delta": response.get("state_delta") or {},
            "options": response.get("options") or [],
            # Backend release metadata always wins over model/n8n output.
            "agent_meta": _agent_meta(execution),
        }
    )


@router.post("/scout", response_model=ScoutResponse)
async def scout(payload: ScoutRequest):
    execution = engine.scout(payload.trip_state, payload.message)
    return _normalize_scout_response(execution)


@router.post("/meridian", response_model=MeridianResponse)
async def meridian(payload: MeridianRequest):
    execution = engine.meridian(payload.trip_state)
    return _normalize_meridian_response(execution)
