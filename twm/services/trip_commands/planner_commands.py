"""Planner-phase (Guide) command handling."""

import copy
from typing import Any

from ...persistence.contracts import RecommendationRecord
from ...prompt_registry import load_prompt_release
from ...schemas.guide import GuideRequest
from ...telemetry import TelemetryLogger
from ..agent_engine import AgentEngine
from ..response_normalization import _normalize_guide_response
from .errors import InvalidTripCommandError


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

    session = planner.get("guide_session", {})
    prior_state = session.get("state", {})
    prior_phase = prior_state.get("phase")
    clarification_resume_phase = session.get("clarification_resume_phase")
    clarification_base_state = session.get("clarification_base_state")
    _validate_guide_event(event, prior_phase)

    if event == "APPROVE_PLAN":
        # Guide has nothing to decide here — the day plan is preserved
        # exactly and the phase moves to PLAN_APPROVED. Doing this
        # deterministically instead of round-tripping through the LLM only
        # to have Backend validate the plan came back unchanged saves a
        # call with no loss of quality: there is no traveler input for
        # Guide to interpret at this event.
        response_message = "Your plan is approved. Generating the detailed itinerary next."
        replacement = copy.deepcopy(prior_state)
        replacement["phase"] = "PLAN_APPROVED"
        explicit_changes: list[str] = []
        agent_meta = {
            "agent": "guide",
            "prompt_version": load_prompt_release("guide").version,
        }
    else:
        guide_input_state = prior_state
        if prior_phase == "NEEDS_CLARIFICATION" and isinstance(
            clarification_base_state, dict
        ):
            guide_input_state = copy.deepcopy(clarification_base_state)
            guide_input_state["phase"] = "NEEDS_CLARIFICATION"
            guide_input_state["pending_clarification"] = prior_state.get(
                "pending_clarification"
            )
        phase = {
            "trip_context": state["trip_context"],
            "guide_state": guide_input_state,
            "guide_event": event,
        }
        request = GuideRequest.model_validate(
            {"event": event, "trip_state": {"trip_context": phase["trip_context"], "guide_state": phase["guide_state"]}, "message": message}
        )
        agent_state = request.trip_state.model_dump(mode="json")
        agent_state["guide_event"] = request.event
        response = _normalize_guide_response(
            await engine.guide(agent_state, request.message)
        )

        if event == "TRAVELER_MESSAGE" and response.outcome == "reopen_destination_discovery":
            return await _reopen_destination_discovery(engine, logger, state, session, message, latest_recommendation)

        replacement = response.guide_state.model_dump(mode="json")
        response_message = response.message
        explicit_changes = list(response.explicit_changes)
        agent_meta = response.agent_meta.model_dump(mode="json")

    _validate_guide_transition(
        event,
        prior_state,
        replacement,
        clarification_resume_phase=clarification_resume_phase,
        clarification_base_state=clarification_base_state,
        explicit_changes=explicit_changes,
    )
    revision = int(session.get("revision", 0)) + 1
    next_session = {
        "state": replacement,
        "revision": revision,
        "explicit_changes": explicit_changes,
    }
    if replacement["phase"] == "NEEDS_CLARIFICATION":
        next_session["clarification_resume_phase"] = (
            clarification_resume_phase
            or ("DAY_PLAN_DRAFT" if event == "APPROVE_PLACES" else None)
            or (
                prior_phase
                if prior_phase in {"PLACES_DRAFT", "DAY_PLAN_DRAFT"}
                else "PLACES_DRAFT"
            )
        )
        base_state = (
            copy.deepcopy(clarification_base_state)
            if isinstance(clarification_base_state, dict)
            else copy.deepcopy(prior_state)
        )
        if event == "TRAVELER_MESSAGE":
            for field in explicit_changes:
                if field != "day_plan":
                    base_state[field] = copy.deepcopy(replacement[field])
        if base_state.get("phase") in {"PLACES_DRAFT", "DAY_PLAN_DRAFT"}:
            next_session["clarification_base_state"] = base_state
    planner["guide_session"] = next_session
    if replacement["phase"] == "PLAN_APPROVED":
        planner["frozen_plan"] = {
            "guide_revision": revision,
            "guide_state": copy.deepcopy(replacement),
        }
        state["active_agent"] = None
        state["stage"] = "planned"
    else:
        state["active_agent"] = "guide"
        state["stage"] = "planning"
    logger.info(
        "Applied Backend-owned Guide revision.",
        event="be.trip.guide.revision_applied",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        guide_event=event,
        guide_phase=replacement["phase"],
        guide_revision=revision,
        frozen=replacement["phase"] == "PLAN_APPROVED",
        explicit_changes=explicit_changes,
    )
    return {
        "message": response_message,
        "agent_meta": agent_meta,
    }


async def _reopen_destination_discovery(
    engine: AgentEngine,
    logger: TelemetryLogger,
    state: dict[str, Any],
    session: dict[str, Any],
    message: str | None,
    latest_recommendation: RecommendationRecord | None,
) -> dict[str, Any]:
    """Backend-validated pre-itinerary Guide -> Meridian reversal.

    Only reachable from a TRAVELER_MESSAGE turn before the plan is frozen
    (frozen_plan is checked before Guide is ever called). Retains the
    superseded Guide session and destination context rather than deleting
    them, then hands off to Meridian within this same command so exactly
    one version increment is committed.
    """
    planner = state["planner_state"]
    superseded = planner.setdefault("superseded_guide_sessions", [])
    superseded.append({
        "guide_session": session,
        "destination_context": state["trip_context"].get("destination"),
    })
    planner.pop("guide_session", None)
    state["trip_context"].pop("destination", None)
    state["trip_context"].pop("selected_option", None)
    state["stage"] = "matching"
    state["active_agent"] = "meridian"
    logger.info(
        "Backend validated a pre-itinerary Guide to Meridian reversal.",
        event="be.trip.guide.reopened_destination_discovery",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        superseded_guide_revision=session.get("revision"),
    )
    from .matcher_commands import apply_meridian

    return await apply_meridian(engine, state, message, latest_recommendation)


def _validate_guide_event(event: str, prior_phase: str | None) -> None:
    if event == "START":
        if prior_phase is not None:
            raise InvalidTripCommandError("Guide planning has already started.")
        return
    if prior_phase is None:
        raise InvalidTripCommandError("Start Guide planning before changing the plan.")
    if event == "APPROVE_PLACES" and prior_phase != "PLACES_DRAFT":
        raise InvalidTripCommandError("Only the latest places draft can be approved.")
    if event == "APPROVE_PLAN" and prior_phase != "DAY_PLAN_DRAFT":
        raise InvalidTripCommandError("Only the latest day plan can be approved.")


def _validate_guide_transition(
    event: str,
    prior_state: dict[str, Any],
    replacement: dict[str, Any],
    *,
    clarification_resume_phase: str | None,
    clarification_base_state: dict[str, Any] | None,
    explicit_changes: list[str],
) -> None:
    next_phase = replacement.get("phase")
    prior_phase = prior_state.get("phase")
    traveler_phases = {
        "NEEDS_CLARIFICATION": {
            "NEEDS_CLARIFICATION",
            clarification_resume_phase or "PLACES_DRAFT",
        },
        "PLACES_DRAFT": {"NEEDS_CLARIFICATION", "PLACES_DRAFT"},
        "DAY_PLAN_DRAFT": {"NEEDS_CLARIFICATION", "DAY_PLAN_DRAFT"},
    }
    allowed_phases = {
        "START": {"NEEDS_CLARIFICATION", "PLACES_DRAFT"},
        "TRAVELER_MESSAGE": traveler_phases.get(prior_phase, set()),
        "APPROVE_PLACES": {"NEEDS_CLARIFICATION", "DAY_PLAN_DRAFT"},
        "APPROVE_PLAN": {"PLAN_APPROVED"},
    }[event]
    if next_phase not in allowed_phases:
        raise InvalidTripCommandError(
            f"Guide returned invalid phase {next_phase} for {event}."
        )
    if event != "TRAVELER_MESSAGE" and explicit_changes:
        raise InvalidTripCommandError(
            f"Guide declared traveler changes for non-traveler event {event}."
        )
    comparison_state = prior_state
    preserved_fields: tuple[str, ...] = ()
    if event in {"APPROVE_PLACES", "APPROVE_PLAN"}:
        preserved_fields = (
            "destinations",
            "duration_days",
            "start_date",
            "places",
            "preferences",
            "exclusions",
        )
        if event == "APPROVE_PLAN":
            preserved_fields += ("day_plan",)
    elif event == "TRAVELER_MESSAGE":
        preserved_fields = (
            "destinations",
            "duration_days",
            "start_date",
            "places",
            "day_plan",
            "preferences",
            "exclusions",
        )
        if isinstance(clarification_base_state, dict):
            comparison_state = clarification_base_state
        if next_phase == "NEEDS_CLARIFICATION":
            preserved_fields = tuple(
                field for field in preserved_fields if field != "day_plan"
            )
    changed = [
        field
        for field in preserved_fields
        if replacement.get(field) != comparison_state.get(field)
    ]
    undeclared = [field for field in changed if field not in explicit_changes]
    if undeclared:
        raise InvalidTripCommandError(
            "Guide changed traveler decisions without declaring them: "
            + ", ".join(undeclared)
            + "."
        )
