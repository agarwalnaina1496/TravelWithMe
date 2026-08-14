"""Scout-phase command handling."""

from typing import Any

from ...persistence.contracts import RecommendationRecord
from ...schemas.scout import ScoutRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_scout_response
from .state import merge_trip_context


async def apply_scout(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    message: str | None,
    latest_recommendation: RecommendationRecord | None = None,
) -> dict[str, Any]:
    phase = {
        "stage": state["stage"],
        "trip_context": state["trip_context"],
        "advisor_state": {
            "conversation_context": state["advisor_state"].get(
                "conversation_context", {}
            )
        },
    }
    request = ScoutRequest.model_validate({"trip_state": phase, "message": message})
    request_data = request.model_dump(mode="json", exclude_none=True)
    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    logger.info(
        f"Received Scout request. Request - {logger.format_json(request_data)}",
        event="be.request.validated",
        source="application",
        agent="scout",
        trip_id=trip_id,
        payload=request_data,
    )
    response = _normalize_scout_response(
        await engine.scout(request.trip_state.model_dump(mode="json"), request.message)
    )
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        f"Returning Scout response. Response - {logger.format_json(response_data)}",
        event="be.response.normalized",
        source="application",
        agent="scout",
        trip_id=trip_id,
        status="success",
        response=response_data,
    )
    delta = response.state_delta.trip_context.model_dump(mode="json")
    delta.pop("selected_option", None)
    merge_trip_context(state["trip_context"], delta)
    if response.message:
        advisor = state["advisor_state"]
        advisor.setdefault("conversation_context", {})[
            "last_advisor_message"
        ] = response.message
    if response.intent == "matcher":
        state["trip_context"].pop("selected_option", None)
        state["stage"] = "matching"
        state["active_agent"] = "meridian"
        from .matcher_commands import apply_meridian

        return await apply_meridian(engine, logger, state, message, latest_recommendation)
    if response.intent == "planner":
        state["stage"] = "planning"
        state["active_agent"] = "guide"
        from .planner_commands import apply_guide

        return await apply_guide(engine, logger, state, "START", None)
    state["active_agent"] = "scout"
    return {
        "message": response.message,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }
