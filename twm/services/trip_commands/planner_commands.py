"""Planner-phase (Guide) command handling."""

import copy
from typing import Any

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
    replacement = response.guide_state.model_dump(mode="json")
    _validate_guide_transition(
        event,
        prior_state,
        replacement,
        clarification_resume_phase=clarification_resume_phase,
        clarification_base_state=clarification_base_state,
        explicit_changes=response.explicit_changes,
    )
    revision = int(session.get("revision", 0)) + 1
    next_session = {
        "state": replacement,
        "revision": revision,
        "explicit_changes": list(response.explicit_changes),
    }
    if response.guide_state.phase == "NEEDS_CLARIFICATION":
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
            for field in response.explicit_changes:
                if field != "day_plan":
                    base_state[field] = copy.deepcopy(replacement[field])
        if base_state.get("phase") in {"PLACES_DRAFT", "DAY_PLAN_DRAFT"}:
            next_session["clarification_base_state"] = base_state
    planner["guide_session"] = next_session
    if response.guide_state.phase == "PLAN_APPROVED":
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
        guide_phase=response.guide_state.phase,
        guide_revision=revision,
        frozen=response.guide_state.phase == "PLAN_APPROVED",
        explicit_changes=list(response.explicit_changes),
    )
    return {
        "message": response.message,
        "agent_meta": response.agent_meta.model_dump(mode="json"),
    }


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
