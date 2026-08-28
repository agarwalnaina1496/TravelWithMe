"""Traveler-composition context update (TWM-213).

Mirrors booking_commands.py's update_booking_dates pattern: a small,
Backend-owned, deterministic write, never Scout/Meridian/Guide extraction.
num_travelers stays a loose conversational fact (like travel_dates) used
only for early planning/affordability judgment -- this command is the one
path that ever writes the structured, booking-precision
trip_context.traveler_composition that Flight/Stay/Trusted Action actually
read.
"""

from typing import Any

from ...schemas.trip_context import TRAVELER_COMPOSITION_KEY, TravelerComposition
from ...telemetry import TelemetryLogger
from .errors import InvalidTripCommandError


def apply_update_traveler_composition(
    logger: TelemetryLogger,
    state: dict[str, Any],
    update: TravelerComposition,
) -> dict[str, Any]:
    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    # Composition is needed as soon as the Board/Itinerary surfaces (and
    # their Transport/Stay drawers) are reachable -- itinerary_state.
    # current_version existing already implies the plan was frozen (Atlas
    # only ever runs against a frozen plan), so no separate freeze check is
    # needed here, unlike update_booking_dates' explicit frozen_plan guard
    # (that one exists specifically for the Dashboard-Bookings-only surface;
    # composition is used earlier too).
    if not state.get("itinerary_state", {}).get("current_version"):
        raise InvalidTripCommandError(
            "Traveler composition can only be updated once Atlas has produced an itinerary."
        )

    trip_context = state["trip_context"]
    previous = trip_context.get(TRAVELER_COMPOSITION_KEY)
    trip_context[TRAVELER_COMPOSITION_KEY] = update.model_dump(mode="json")

    logger.info(
        "Updated traveler composition for a trip.",
        event="be.trip.traveler_composition.updated",
        source="application",
        trip_id=trip_id,
        previous_total=previous.get("adults", 0) + previous.get("children", 0) + previous.get("infants", 0)
        if isinstance(previous, dict)
        else None,
        new_total=update.total,
    )
    return {"message": None, "agent_meta": None}
