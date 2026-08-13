"""Planner-phase (Guide) command handling."""

from typing import Any

from ...persistence.contracts import RecommendationRecord
from ...schemas.guide import GuideRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_guide_response
from .errors import InvalidTripCommandError
from .state import merge_trip_context


def guide_has_started(state: dict[str, Any]) -> bool:
    planner = state["planner_state"]
    return bool(
        planner.get("places")
        or planner.get("day_plan")
        or planner.get("conversation_context", {}).get("awaiting")
        or planner.get("frozen_plan")
    )


def _guide_planner_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    planner = state["planner_state"]
    return {
        "conversation_context": planner.get("conversation_context") or {},
        "places": planner.get("places") or [],
        "day_plan": planner.get("day_plan") or [],
    }


async def apply_guide(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    event: str,
    message: str | None,
    latest_recommendation: RecommendationRecord | None = None,
) -> dict[str, Any]:
    planner = state["planner_state"]
    if planner.get("frozen_plan"):
        raise InvalidTripCommandError(
            "The approved plan is frozen and cannot be changed."
        )

    _validate_guide_event(event, state)

    if event == "APPROVE_PLAN":
        # Guide has nothing to decide here — the day plan is preserved
        # exactly and the plan freezes. Doing this deterministically instead
        # of round-tripping through the LLM only to have Backend validate
        # the plan came back unchanged saves a call with no loss of
        # quality: there is no traveler input for Guide to interpret here.
        return _apply_plan_freeze(logger, state)

    if event == "APPROVE_PLACES" and not state["trip_context"].get("trip_duration"):
        raise InvalidTripCommandError(
            "Trip duration is required before building a day plan."
        )
    if event == "APPROVE_PLACES" and not planner.get("places"):
        raise InvalidTripCommandError("Approve places before building a day plan.")

    request = GuideRequest.model_validate(
        {
            "event": event,
            "trip_state": {
                "trip_context": state["trip_context"],
                "planner_state": _guide_planner_snapshot(state),
            },
            "message": message,
        }
    )
    agent_state = request.trip_state.model_dump(mode="json")
    agent_state["guide_event"] = request.event
    response = _normalize_guide_response(
        await engine.guide(agent_state, request.message)
    )

    if event == "TRAVELER_MESSAGE" and response.outcome == "reopen_destination_discovery":
        return await _reopen_destination_discovery(
            engine, logger, state, message, latest_recommendation
        )

    delta = response.state_delta
    merge_trip_context(state["trip_context"], delta.trip_context.model_dump(mode="json"))

    planner_delta = delta.planner_state
    if planner_delta.conversation_context is not None:
        planner.setdefault("conversation_context", {})["awaiting"] = (
            planner_delta.conversation_context.awaiting
        )
    if planner_delta.places is not None:
        planner["places"] = list(planner_delta.places)
    if planner_delta.day_plan is not None:
        planner["day_plan"] = [
            day.model_dump(mode="json") for day in planner_delta.day_plan
        ]

    _validate_guide_transition(event, state, planner_delta)
    planner["revision"] = int(planner.get("revision", 0)) + 1

    state["active_agent"] = "guide"
    state["stage"] = "planning"
    logger.info(
        "Applied Backend-owned Guide revision.",
        event="be.trip.guide.revision_applied",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        guide_event=event,
        guide_revision=planner["revision"],
        awaiting=planner.get("conversation_context", {}).get("awaiting"),
        places_count=len(planner.get("places") or []),
        day_plan_length=len(planner.get("day_plan") or []),
    )
    return {
        "message": response.message,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }


def _apply_plan_freeze(
    logger: TelemetryLogger, state: dict[str, Any]
) -> dict[str, Any]:
    planner = state["planner_state"]
    trip_context = state["trip_context"]
    guide_state = {
        "destinations": trip_context.get("destinations") or [],
        "trip_duration": trip_context.get("trip_duration"),
        "start_date": trip_context.get("start_date"),
        "places": planner.get("places") or [],
        "day_plan": planner.get("day_plan") or [],
    }
    revision = int(planner.get("revision", 0)) + 1
    planner["revision"] = revision
    planner["frozen_plan"] = {"guide_state": guide_state, "guide_revision": revision}
    state["active_agent"] = None
    state["stage"] = "planned"
    logger.info(
        "Applied Backend-owned Guide revision.",
        event="be.trip.guide.revision_applied",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        guide_event="APPROVE_PLAN",
        guide_revision=revision,
        frozen=True,
    )
    return {
        "message": "Your plan is approved. Generating the detailed itinerary next.",
        "agent_meta": None,
    }


async def _reopen_destination_discovery(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    message: str | None,
    latest_recommendation: RecommendationRecord | None,
) -> dict[str, Any]:
    """Backend-validated pre-itinerary Guide -> Meridian reversal.

    Only reachable from a TRAVELER_MESSAGE turn before the plan is frozen
    (frozen_plan is checked before Guide is ever called). Retains the
    superseded planner state rather than deleting it, then hands off to
    Meridian within this same command so exactly one version increment is
    committed.
    """
    planner = state["planner_state"]
    superseded = planner.setdefault("superseded_planner_states", [])
    superseded.append(
        {
            "planner_state": _guide_planner_snapshot(state),
            "destination_context": state["trip_context"].get("destinations")
            or state["trip_context"].get("destination"),
        }
    )
    planner["conversation_context"] = {}
    planner["places"] = []
    planner["day_plan"] = []
    state["trip_context"].pop("destination", None)
    state["trip_context"].pop("destinations", None)
    state["trip_context"].pop("selected_option", None)
    state["stage"] = "matching"
    state["active_agent"] = "meridian"
    logger.info(
        "Backend validated a pre-itinerary Guide to Meridian reversal.",
        event="be.trip.guide.reopened_destination_discovery",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
    )
    from .matcher_commands import apply_meridian

    return await apply_meridian(engine, state, message, latest_recommendation)


def _validate_guide_event(event: str, state: dict[str, Any]) -> None:
    started = guide_has_started(state)
    if event == "START":
        if started:
            raise InvalidTripCommandError("Guide planning has already started.")
        return
    if not started:
        raise InvalidTripCommandError("Start Guide planning before changing the plan.")
    if event == "APPROVE_PLACES" and not state["planner_state"].get("places"):
        raise InvalidTripCommandError("Only the latest places draft can be approved.")
    if event == "APPROVE_PLAN" and not state["planner_state"].get("day_plan"):
        raise InvalidTripCommandError("Only the latest day plan can be approved.")


def _validate_guide_transition(
    event: str, state: dict[str, Any], planner_delta: Any
) -> None:
    if event == "START" and planner_delta.day_plan is not None:
        raise InvalidTripCommandError(
            "Guide returned a day plan for START, which does not build one."
        )
    if event == "APPROVE_PLACES" and planner_delta.day_plan is None:
        awaiting = state["planner_state"].get("conversation_context", {}).get("awaiting")
        if not awaiting:
            raise InvalidTripCommandError(
                "Guide did not return a day plan for APPROVE_PLACES."
            )
    if planner_delta.day_plan is not None:
        _validate_day_plan(state)


def _validate_day_plan(state: dict[str, Any]) -> None:
    planner = state["planner_state"]
    day_plan = planner.get("day_plan") or []
    places = planner.get("places") or []
    trip_duration = state["trip_context"].get("trip_duration")
    if trip_duration is None:
        raise InvalidTripCommandError("Guide returned a day plan without a known duration.")
    if len(day_plan) != trip_duration:
        raise InvalidTripCommandError("Day plan length must equal trip_duration.")
    day_numbers = [day["day_number"] for day in day_plan]
    if day_numbers != list(range(1, trip_duration + 1)):
        raise InvalidTripCommandError("Day numbers must be sequential from 1.")
    allocated = [place for day in day_plan for place in day["places"]]
    if len(allocated) != len({place.casefold() for place in allocated}):
        raise InvalidTripCommandError("Each place must be allocated exactly once.")
    if {place.casefold() for place in allocated} != {
        place.casefold() for place in places
    }:
        raise InvalidTripCommandError(
            "Day plan must allocate every approved place and no others."
        )
