"""Normalize workflow responses into validated API contracts."""

from ..schemas import (
    AgentMeta,
    AtlasResponse,
    GuideResponse,
    MeridianResponse,
    ScoutResponse,
)
from .agent_engine import AgentExecution


def _agent_meta(execution: AgentExecution) -> AgentMeta:
    release = execution.prompt_release
    return AgentMeta(agent=release.agent, prompt_version=release.version)


def _normalize_scout_response(execution: AgentExecution) -> ScoutResponse:
    response = execution.response
    return ScoutResponse(
        message=response.get("message") or "",
        state_delta=response.get("state_delta") or {},
        intent=response.get("intent"),
        agent_meta=_agent_meta(execution),
    )


def _normalize_guide_response(execution: AgentExecution) -> GuideResponse:
    response = execution.response
    return GuideResponse(
        message=response.get("message") or "",
        state_delta=response.get("state_delta") or {},
        outcome=response.get("outcome") or "continue",
        agent_meta=_agent_meta(execution),
    )


def _normalize_atlas_response(execution: AgentExecution) -> AtlasResponse:
    response = execution.response
    return AtlasResponse(
        final_itinerary=response.get("final_itinerary") or {},
        unresolved=response.get("unresolved") or [],
        agent_meta=_agent_meta(execution),
    )


def _normalize_meridian_response(execution: AgentExecution) -> MeridianResponse:
    response = execution.response
    normalized = {
        "status": response.get("status") or "HARD_FAIL",
        "message": response.get("message") or "",
        "state_delta": response.get("state_delta") or {},
        "generated_at": response.get("generated_at"),
        "trip_type": response.get("trip_type"),
        "options": response.get("options") or [],
        # Backend release metadata always wins over model/n8n output.
        "agent_meta": _agent_meta(execution),
    }
    if "traveler_criteria" in response:
        normalized["traveler_criteria"] = response["traveler_criteria"]
    if "constraint_adjustment_suggestions" in response:
        normalized["constraint_adjustment_suggestions"] = response[
            "constraint_adjustment_suggestions"
        ]
    return MeridianResponse(**normalized)
