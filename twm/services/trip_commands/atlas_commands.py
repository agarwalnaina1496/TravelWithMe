"""Itinerary-phase (Atlas) command handling."""

from typing import Any

from ...schemas.atlas import AtlasRequest, AtlasWorkingPlan
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_atlas_response
from .errors import InvalidTripCommandError


async def apply_atlas(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
) -> dict[str, Any]:
    frozen_plan = state["planner_state"].get("frozen_plan")
    if not frozen_plan:
        raise InvalidTripCommandError(
            "Approve a Guide plan before generating an itinerary."
        )

    itinerary = state["itinerary_state"]
    if itinerary.get("status") == "ready":
        result = itinerary["current_version"]["result"]
        return {"message": None, "agent_meta": result["agent_meta"]}

    working_plan = build_working_plan(frozen_plan["guide_state"])
    request = AtlasRequest.model_validate(
        {"trip_context": state["trip_context"], "working_plan": working_plan.model_dump(mode="json")}
    )
    agent_state = request.model_dump(mode="json")
    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    logger.info(
        f"Received Atlas request. Request - {logger.format_json(agent_state)}",
        event="be.request.validated",
        source="application",
        agent="atlas",
        trip_id=trip_id,
        payload=agent_state,
    )
    response = _normalize_atlas_response(await engine.atlas(agent_state, None))
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        f"Returning Atlas response. Response - {logger.format_json(response_data)}",
        event="be.response.normalized",
        source="application",
        agent="atlas",
        trip_id=trip_id,
        status="success",
        response=response_data,
    )

    state["itinerary_state"] = {
        "status": "ready",
        "current_version": {
            "version": 1,
            "source_guide_revision": frozen_plan["guide_revision"],
            "result": response.model_dump(mode="json"),
        },
        "proposed_revision": None,
    }
    logger.info(
        "Generated Backend-owned initial Atlas itinerary.",
        event="be.trip.atlas.itinerary_generated",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        source_guide_revision=frozen_plan["guide_revision"],
        version=1,
    )
    return {
        "message": None,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }


def build_working_plan(guide_state: dict[str, Any]) -> AtlasWorkingPlan:
    return AtlasWorkingPlan.model_validate(
        {
            "destinations": guide_state["destinations"],
            "trip_duration": guide_state["trip_duration"],
            "approved_places": guide_state["places"],
            "days": [
                {
                    "day_number": day["day_number"],
                    "date": day.get("date"),
                    "places": day["places"],
                }
                for day in guide_state["day_plan"]
            ],
        }
    )
