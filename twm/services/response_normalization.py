"""Normalize workflow responses into validated API contracts."""

from typing import Any

from ..schemas import AgentMeta, MeridianResponse, ScoutResponse
from .agent_engine import AgentExecution


def _unwrap_agent_response(raw_response: Any) -> dict[str, Any]:
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
