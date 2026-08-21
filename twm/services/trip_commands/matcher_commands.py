"""Matcher-phase (Meridian) command handling."""

from typing import Any

from ...persistence.contracts import RecommendationRecord
from ...schemas.meridian import MeridianRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_meridian_response
from .errors import InvalidTripCommandError
from .state import merge_operational_state, merge_trip_context, set_stage


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
    logger: TelemetryLogger,
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
    request_data = request.model_dump(mode="json", exclude_none=True)
    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    logger.info(
        f"Received Meridian request. Request - {logger.format_json(request_data)}",
        event="be.request.validated",
        source="application",
        agent="meridian",
        trip_id=trip_id,
        payload=request_data,
    )
    response = _normalize_meridian_response(
        await engine.meridian(request.trip_state.model_dump(mode="json"), request.message)
    )
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        f"Returning Meridian response. Response - {logger.format_json(response_data)}",
        event="be.response.normalized",
        source="application",
        agent="meridian",
        trip_id=trip_id,
        status="success",
        response=response_data,
    )
    # No selected_option/recommendations pop needed here — MeridianStateDelta's
    # own reject_ui_owned_state validator already raises before this point if
    # either is present, so response.state_delta can never carry them.
    trip_delta = response.state_delta.trip_context.model_dump(mode="json")
    merge_trip_context(state["trip_context"], trip_delta)
    matcher_delta = dict(response.state_delta.matcher_state)
    merge_operational_state(state["matcher_state"], matcher_delta)
    result: dict[str, Any] = {
        "message": response.message,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }
    if response.status == "NEEDS_CLARIFICATION":
        # No stage write here: apply_meridian only ever runs once stage is
        # already "matching" (set upstream by discover_entry, Scout's
        # matcher handoff, or the refinement paths) — re-asserting the same
        # value was a no-op, not a real transition (TWM-188).
        state["active_agent"] = "meridian"
    else:
        payload = response.model_dump(mode="json", exclude={"state_delta"}, exclude_none=True)
        payload["version"] = (latest.version if latest else 0) + 1
        result["new_recommendation"] = payload
        set_stage(state, "recommended")
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


def select_destination(
    logger: TelemetryLogger,
    state: dict[str, Any],
    option_id: str,
    latest: RecommendationRecord | None,
) -> dict[str, Any]:
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
    # Backend/UI identity state, not a traveler-provided fact — lives as
    # its own top-level branch (twm/services/trip_commands/state.py),
    # never inside trip_context. No agent reads it back on any later turn;
    # its only readers are Destinations.jsx's re-selection matching and
    # TripPreview.jsx's entry-path analytics label, both cross-phase UI
    # concerns, not Meridian's own operational memory.
    state["selected_option"] = {
        "type": option["type"], "id": identity, "name": option["name"]
    }
    # The one canonical "what's the destination" signal (twm/schemas/
    # trip_context.py) — written here for the Discover path exactly like
    # Guide writes it for the known-destination path, so every downstream
    # reader (Guide's own gate, plan-freeze, the trip summary/recap) has
    # exactly one field to check.
    state["trip_context"]["destinations"] = [option["name"]]
    set_stage(state, "matched")
    state["active_agent"] = None
    logger.info(
        "Applied Backend-owned destination selection.",
        event="be.trip.matcher.destination_selected",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        option_type=option["type"],
        option_id=identity,
    )
    return {"message": f"{option['name']} is confirmed.", "agent_meta": None}
