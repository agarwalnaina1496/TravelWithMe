"""Guide plan rules: what makes a Guide response acceptable for the trip.

One home for the rules a Guide `state_delta` has to satisfy once merged into the
trip -- the day plan covering exactly the trip's days and approved places, and a
cleared final gate carrying a plan. They run in two places that must never
disagree: as a pre-acceptance review (so a violation is retried with the rule
stated) and on the real apply (the same function, on the live state).
"""

import copy
from typing import Any

from pydantic import ValidationError

from ...schemas.atlas import MAX_TRIP_DAYS
from ...schemas.guide import GuideAgentOutput
from ...schemas.trip_context import DESTINATIONS_KEY, TRIP_DURATION_KEY
from ..agent_engine import OutputReview
from .atlas_commands import build_working_plan
from .errors import InvalidTripCommandError
from .state import merge_operational_state, merge_trip_context


def merge_guide_delta(
    state: dict[str, Any], delta: Any, previous_awaiting: str | None
) -> None:
    """Apply a Guide `state_delta` to `state` and enforce the plan rules.

    One function for both the real apply and the pre-acceptance review, so the
    rules cannot drift between them.
    """

    merge_trip_context(state["trip_context"], delta.trip_context.model_dump(mode="json"))

    # Same shared primitive apply_meridian uses for matcher_state — an
    # agent's own operational memory merges the same way regardless of
    # which specialist owns it: a dict field recurses (conversation_context
    # overwrites just its awaiting key), everything else replaces wholesale
    # when included (places/day_plan), and an omitted field is left alone.
    planner_delta = delta.planner_state
    planner_delta_dict: dict[str, Any] = {}
    if planner_delta.conversation_context is not None:
        planner_delta_dict["conversation_context"] = (
            planner_delta.conversation_context.model_dump(mode="json")
        )
    if planner_delta.places is not None:
        planner_delta_dict["places"] = list(planner_delta.places)
    if planner_delta.day_plan is not None:
        planner_delta_dict["day_plan"] = [
            day.model_dump(mode="json") for day in planner_delta.day_plan
        ]
    merge_operational_state(state["planner_state"], planner_delta_dict)

    validate_guide_transition(state, planner_delta, previous_awaiting)


def guide_review(state: dict[str, Any], previous_awaiting: str | None) -> OutputReview:
    """The Guide plan rules, judged on a copy of the trip before the response
    is accepted -- so a plan that breaks them is retried with the rule stated
    instead of failing the traveler's turn afterwards."""

    def review(response: dict[str, Any]) -> list[str]:
        if response.get("outcome") == "reopen_destination_discovery":
            return []  # nothing is merged on that path
        # Only these two branches are read or written by the rules.
        probe = {key: copy.deepcopy(state[key]) for key in ("trip_context", "planner_state")}
        try:
            merge_guide_delta(
                probe, GuideAgentOutput.model_validate(response).state_delta, previous_awaiting
            )
        except InvalidTripCommandError as error:
            return [str(error)]
        return []

    return review


def validate_guide_transition(
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
        validate_day_plan(state)


def coerce_trip_duration(trip_duration: Any) -> int:
    # trip_duration is a deliberately untyped free-text trip_context field
    # (Scout/Guide extraction). A numeral string ("5") compares unequal to
    # every int via `!=`, silently rejecting a correct plan; a float (5.0,
    # plausible from an LLM-emitted JSON number) passes the length check but
    # crashes range() with an uncaught TypeError. Coerce explicitly instead
    # of trusting either shape as-is.
    if isinstance(trip_duration, bool):
        raise InvalidTripCommandError("trip_duration must be a whole number of days.")
    if isinstance(trip_duration, int):
        return trip_duration
    if isinstance(trip_duration, float):
        if not trip_duration.is_integer():
            raise InvalidTripCommandError("trip_duration must be a whole number of days.")
        return int(trip_duration)
    if isinstance(trip_duration, str):
        try:
            return int(trip_duration.strip())
        except ValueError:
            raise InvalidTripCommandError(
                "trip_duration must be a whole number of days."
            ) from None
    raise InvalidTripCommandError("trip_duration must be a whole number of days.")


def validate_day_plan(state: dict[str, Any]) -> None:
    """The plan rules (length == trip_duration, sequential days, each approved
    place allocated exactly once) live in `AtlasWorkingPlan` -- the same model
    Atlas is later handed -- so a plan Guide may return is exactly a plan Atlas
    can accept."""

    planner = state["planner_state"]
    trip_context = state["trip_context"]
    trip_duration = trip_context.get(TRIP_DURATION_KEY)
    if trip_duration is None:
        raise InvalidTripCommandError("Guide returned a day plan without a known duration.")
    try:
        build_working_plan(
            {
                DESTINATIONS_KEY: trip_context.get(DESTINATIONS_KEY) or [],
                TRIP_DURATION_KEY: coerce_trip_duration(trip_duration),
                "places": planner.get("places") or [],
                "day_plan": planner.get("day_plan") or [],
            }
        )
    except ValidationError as error:
        raise InvalidTripCommandError(plain_day_plan_reason(error)) from None


def plain_day_plan_reason(error: ValidationError) -> str:
    """A day-plan failure in words a traveler (and the model, on a retry) can
    act on. The plan rules we wrote are already plain; the generic type and
    length checks are translated rather than echoed as library text."""

    first = error.errors()[0]
    if first["type"] == "value_error":
        reason = first["msg"].removeprefix("Value error, ")
        return reason[:1].upper() + reason[1:] + "."
    field = first["loc"][0] if first["loc"] else None
    if field == "trip_duration":
        return f"A trip can be planned for 1 to {MAX_TRIP_DAYS} days."
    if field in {"destinations", "approved_places", "days"}:
        return "Destination and place names cannot be blank."
    return "The day plan could not be accepted."
