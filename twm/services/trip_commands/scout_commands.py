"""Scout-phase command handling."""

from typing import Any

from ...schemas.scout import ScoutRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_scout_response
from .state import deep_merge


async def _call_scout_and_merge_context(
    engine: AgentEngine,
    state: dict[str, Any],
    message: str | None,
):
    """Calls Scout and merges its extracted trip_context delta. Returns the
    raw normalized response so callers can decide what to do with
    `.intent`/`.message` — this function makes no routing or persistence
    decision of its own."""
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
    response = _normalize_scout_response(
        await engine.scout(request.trip_state.model_dump(mode="json"), request.message)
    )
    delta = dict(response.state_delta.trip_context)
    delta.pop("selected_option", None)
    deep_merge(state["trip_context"], delta)
    return response


async def apply_scout_extraction_only(
    engine: AgentEngine,
    state: dict[str, Any],
    message: str,
) -> None:
    """Plan-a-trip's "anything else" field (discover_entry/known_destination_entry):
    calls Scout purely to extract trip_context signals from free text. The
    command that called this already determined routing deterministically
    (by virtue of being discover_entry or known_destination_entry), so
    Scout's own `intent` decision and conversational `message` are discarded
    here — nothing is shown to the traveler from this call, and no
    advisor_state history is written for a message no one saw."""
    await _call_scout_and_merge_context(engine, state, message)


async def apply_scout(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    message: str | None,
) -> dict[str, Any]:
    response = await _call_scout_and_merge_context(engine, state, message)
    if response.message:
        advisor = state["advisor_state"]
        advisor.setdefault("conversation_context", {})[
            "last_advisor_message"
        ] = response.message
        advisor.setdefault("artifacts", []).append(
            {
                "type": "advice",
                "source": "scout",
                "assistant_message": response.message,
                "agent_meta": response.agent_meta.model_dump(mode="json"),
            }
        )
    if response.intent == "matcher":
        state["trip_context"].pop("selected_option", None)
        state["stage"] = "matching"
        state["active_agent"] = "meridian"
        from .matcher_commands import apply_meridian

        return await apply_meridian(engine, state, message)
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
