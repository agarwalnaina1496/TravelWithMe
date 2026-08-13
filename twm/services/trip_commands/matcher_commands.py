"""Matcher-phase (Meridian) command handling."""

from typing import Any

from ...persistence.contracts import RecommendationRecord
from ...schemas.meridian import MeridianRequest
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_meridian_response
from .errors import InvalidTripCommandError
from .state import merge_operational_state, merge_trip_context


def _prior_options(latest: RecommendationRecord | None) -> list[dict[str, Any]]:
    if not latest:
        return []
    return [
        {
            "rank": option["rank"], "name": option["name"],
            "type": option["type"],
            **({"circuit_id": option["circuit_id"]} if option["type"] == "circuit" else {"destination_id": option["destination_id"]}),
        }
        for option in latest.options
    ]


async def apply_meridian(
    engine: AgentEngine,
    state: dict[str, Any],
    message: str | None,
    latest: RecommendationRecord | None,
    refinement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior_options = _prior_options(latest)
    matcher_state: dict[str, Any] = {
        "conversation_context": state["matcher_state"].get("conversation_context", {}),
        "prior_recommendations": prior_options,
        "rejected_options": state["matcher_state"].get("rejected_options", []),
    }
    if refinement is not None:
        _validate_refinement_reference(refinement, prior_options)
        matcher_state["refinement"] = refinement
    phase = {
        "trip_context": state["trip_context"],
        "advisor_state": {
            "conversation_context": state["advisor_state"].get("conversation_context", {})
        },
        "matcher_state": matcher_state,
    }
    request = MeridianRequest.model_validate({"trip_state": phase, "message": message})
    response = _normalize_meridian_response(
        await engine.meridian(request.trip_state.model_dump(mode="json"), request.message)
    )
    trip_delta = response.state_delta.trip_context.model_dump(mode="json")
    trip_delta.pop("selected_option", None)
    merge_trip_context(state["trip_context"], trip_delta)
    matcher_delta = dict(response.state_delta.matcher_state)
    matcher_delta.pop("recommendations", None)
    merge_operational_state(state["matcher_state"], matcher_delta)
    result: dict[str, Any] = {
        "message": response.message,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }
    if response.status == "NEEDS_CLARIFICATION":
        state["stage"] = "matching"
        state["active_agent"] = "meridian"
    else:
        payload = response.model_dump(mode="json", exclude={"state_delta"}, exclude_none=True)
        payload["version"] = (latest.version if latest else 0) + 1
        result["new_recommendation"] = payload
        state["stage"] = "recommended"
        state["active_agent"] = None
    return result


def _validate_refinement_reference(
    refinement: dict[str, Any], prior_options: list[dict[str, Any]]
) -> None:
    reference = refinement.get("reference", {})
    reference_type = reference.get("type")
    reference_id = reference.get("id")
    known = any(
        option["type"] == reference_type
        and reference_id
        in {option.get("destination_id"), option.get("circuit_id")}
        for option in prior_options
    )
    if not known:
        raise InvalidTripCommandError(
            "More like this reference does not match a known recommendation option."
        )


def select_destination(state: dict[str, Any], option_id: str, latest: RecommendationRecord | None) -> dict[str, Any]:
    if not latest:
        raise InvalidTripCommandError("No recommendation is available to select.")
    option = next(
        (
            item
            for item in latest.options
            if option_id in {item.get("destination_id"), item.get("circuit_id")}
        ),
        None,
    )
    if option is None:
        raise InvalidTripCommandError("Selected option is not in the latest recommendations.")
    identity = option.get("circuit_id") or option.get("destination_id")
    state["trip_context"]["selected_option"] = {
        "type": option["type"], "id": identity, "name": option["name"]
    }
    state["stage"] = "matched"
    state["active_agent"] = None
    return {"message": f"{option['name']} is confirmed.", "agent_meta": None}
