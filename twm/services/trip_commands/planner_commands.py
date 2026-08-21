"""Planner-phase (Guide) command handling."""

from typing import Any

from ...persistence.contracts import RecommendationRecord
from ...schemas.guide import GuideRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_guide_response
from .errors import InvalidTripCommandError
from .state import merge_trip_context, set_stage


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

    if event == "APPROVE_PLAN":
        # Guide has nothing to decide here — the day plan is preserved
        # exactly and the plan freezes. Doing this deterministically instead
        # of round-tripping through the LLM only to have Backend validate
        # the plan came back unchanged saves a call with no loss of
        # quality: there is no traveler input for Guide to interpret here.
        if not state["planner_state"].get("day_plan"):
            raise InvalidTripCommandError("Only the latest day plan can be approved.")
        return _apply_plan_freeze(logger, state)

    started_before_this_turn = guide_has_started(state)
    previous_awaiting = planner.get("conversation_context", {}).get("awaiting")
    previous_day_plan_empty = not planner.get("day_plan")

    # TWM-188 item 8: a revision request on an already-ready plan
    # transiently flips stage back to "planning" while Guide reprocesses,
    # mirroring recommended<->matching — day_plan itself isn't cleared
    # here, only replaced/left unchanged once Guide responds below.
    if state.get("stage") == "plan_ready":
        set_stage(state, "planning", logger, context="guide_revision_request")

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
    # No guide_event field on the agent payload — every MESSAGE turn is
    # handled identically (see guide.md), so there is nothing for Guide to
    # branch on. APPROVE_PLAN never reaches here (handled above).
    agent_state = request.trip_state.model_dump(mode="json")
    request_data = request.model_dump(mode="json", exclude_none=True)
    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    logger.info(
        f"Received Guide request. Request - {logger.format_json(request_data)}",
        event="be.request.validated",
        source="application",
        agent="guide",
        trip_id=trip_id,
        payload=request_data,
    )
    response = _normalize_guide_response(
        await engine.guide(agent_state, request.message)
    )
    response_data = response.model_dump(mode="json", exclude_none=True)
    logger.info(
        f"Returning Guide response. Response - {logger.format_json(response_data)}",
        event="be.response.normalized",
        source="application",
        agent="guide",
        trip_id=trip_id,
        status="success",
        response=response_data,
    )

    # Reconsidering the destination is only meaningful once there's an
    # existing gate/plan to reconsider — not on the cold first call, which
    # has nothing yet to walk back from.
    if started_before_this_turn and response.outcome == "reopen_destination_discovery":
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

    _validate_guide_transition(state, planner_delta, previous_awaiting)
    planner["revision"] = int(planner.get("revision", 0)) + 1

    places_count = len(planner.get("places") or [])
    day_plan_length = len(planner.get("day_plan") or [])

    if previous_day_plan_empty and day_plan_length:
        trip_context = state["trip_context"]
        logger.info(
            "Guide generated the complete plan in a single step.",
            event="be.trip.guide.plan_generated",
            source="application",
            trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
            guide_revision=planner["revision"],
            places_count=places_count,
            day_plan_length=day_plan_length,
            budget_present=bool(trip_context.get("budget")),
            preferences_present=bool(trip_context.get("preferences")),
        )

    # TWM-188 item 8: plan_ready mirrors recommended's role on the Discover
    # side — set whenever day_plan is non-empty after this turn, whether
    # this is the first draft or a subsequent revision response. Every
    # other apply_guide turn (still-gating, empty day_plan) leaves stage at
    # "planning", set upstream by start_planning/entry_intent="known_
    # destination"/Scout's planner handoff.
    if day_plan_length:
        set_stage(state, "plan_ready", logger, context="guide_plan_ready")

    state["active_agent"] = "guide"
    logger.info(
        "Applied Backend-owned Guide revision.",
        event="be.trip.guide.revision_applied",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        guide_event=event,
        guide_revision=planner["revision"],
        awaiting=planner.get("conversation_context", {}).get("awaiting"),
        places_count=places_count,
        day_plan_length=day_plan_length,
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
    set_stage(state, "planned")
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


REOPEN_CHOICE_AWAITING = "destination_reopen_choice"


def _supersede_planner_state(state: dict[str, Any]) -> None:
    """Shared by both reopen paths (fresh discovery and revisiting an
    existing list) — either way the current places/day_plan are being
    abandoned in favor of a destination decision that hasn't happened yet,
    so both retain the superseded snapshot rather than deleting it."""
    planner = state["planner_state"]
    superseded = planner.setdefault("superseded_planner_states", [])
    superseded.append(
        {
            "planner_state": _guide_planner_snapshot(state),
            "destination_context": state["trip_context"].get("destinations"),
        }
    )
    planner["conversation_context"] = {}
    planner["places"] = []
    planner["day_plan"] = []
    state["trip_context"].pop("destinations", None)
    state["selected_option"] = None


def has_pending_reopen_choice(state: dict[str, Any]) -> bool:
    awaiting = state["planner_state"].get("conversation_context", {}).get("awaiting")
    return awaiting == REOPEN_CHOICE_AWAITING


async def _reopen_destination_discovery(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    message: str | None,
    latest_recommendation: RecommendationRecord | None,
) -> dict[str, Any]:
    """Backend-validated pre-itinerary Guide reversal — only reachable once
    Guide has already started (apply_guide's started_before_this_turn
    guard) and before the plan is frozen (frozen_plan is checked before
    Guide is ever called).

    History-aware (TWM-188 item 3): a known-destination-direct trip
    (latest_recommendation is None, Meridian never ran) has only one
    sensible target and reverses immediately. A trip that already has a
    recommendation list from Discover doesn't silently pick a stage —
    it prompts the traveler to choose revisit-existing vs. start-fresh
    (resolved by reopen_destination_revisit/reopen_destination_fresh).
    """
    if latest_recommendation is not None:
        return _prompt_reopen_choice(logger, state)
    return await apply_reopen_fresh(engine, logger, state, message, latest_recommendation)


def _prompt_reopen_choice(
    logger: TelemetryLogger, state: dict[str, Any]
) -> dict[str, Any]:
    planner = state["planner_state"]
    planner.setdefault("conversation_context", {})["awaiting"] = REOPEN_CHOICE_AWAITING
    logger.info(
        "Guide reversal found an existing recommendation list — asking the "
        "traveler to choose revisit vs. fresh discovery instead of "
        "silently picking one.",
        event="be.trip.guide.reopen_choice_prompted",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
    )
    return {
        "message": (
            "You already have destination recommendations from earlier — "
            "want to revisit that list, or start a fresh search?"
        ),
        "agent_meta": None,
    }


async def apply_reopen_fresh(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    message: str | None,
    latest_recommendation: RecommendationRecord | None,
) -> dict[str, Any]:
    """The traveler wants a new Meridian conversation — either the
    immediate reversal (no prior recommendations existed) or their
    explicit choice after being prompted (reopen_destination_fresh)."""
    _supersede_planner_state(state)
    set_stage(state, "matching", logger, context="reopen_destination_fresh")
    state["active_agent"] = "meridian"
    logger.info(
        "Backend validated a pre-itinerary Guide to Meridian reversal.",
        event="be.trip.guide.reopened_destination_discovery",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        choice="fresh",
    )
    from .matcher_commands import apply_meridian

    return await apply_meridian(engine, logger, state, message, latest_recommendation)


def apply_reopen_revisit(
    logger: TelemetryLogger, state: dict[str, Any]
) -> dict[str, Any]:
    """The traveler chose to go back to their existing recommendation list
    instead of a fresh Meridian conversation — no new matcher round is
    triggered, the already-archived recommendations are still intact."""
    _supersede_planner_state(state)
    set_stage(state, "recommended", logger, context="reopen_destination_revisit")
    state["active_agent"] = None
    logger.info(
        "Backend validated a pre-itinerary Guide reversal back to an "
        "existing recommendation list.",
        event="be.trip.guide.reopened_destination_discovery",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        choice="revisit",
    )
    return {
        "message": "Here are the destinations Meridian already found for you.",
        "agent_meta": None,
    }


def _validate_guide_transition(
    state: dict[str, Any], planner_delta: Any, previous_awaiting: str | None
) -> None:
    if (
        previous_awaiting == "anything_else"
        and not state["planner_state"].get("conversation_context", {}).get("awaiting")
        and planner_delta.day_plan is None
    ):
        raise InvalidTripCommandError(
            "Guide cleared the final gating question without generating a plan."
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
