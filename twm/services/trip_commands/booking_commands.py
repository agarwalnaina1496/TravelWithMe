"""Post-freeze booking-date context update (TWM-201)."""

from typing import Any

from ...schemas.trip_context import BOOKING_DATE_KEY
from ...schemas.trips import TripBookingDateInput
from ...telemetry import TelemetryLogger


def apply_update_booking_dates(
    logger: TelemetryLogger,
    state: dict[str, Any],
    update: TripBookingDateInput,
) -> dict[str, Any]:
    trip_id = str(state.get("trip_id")) if state.get("trip_id") else None
    trip_context = state["trip_context"]
    previous = trip_context.get(BOOKING_DATE_KEY)
    previous_precision = previous.get("precision") if isinstance(previous, dict) else None

    if update.departure_date is not None:
        new_precision = "exact"
        trip_context[BOOKING_DATE_KEY] = {
            "precision": new_precision,
            "departure_date": update.departure_date.isoformat(),
        }
    else:
        new_precision = "month"
        trip_context[BOOKING_DATE_KEY] = {
            "precision": new_precision,
            "departure_month": update.departure_month,
        }

    # Booking-search precision only (TWM-201 MVP boundary) — this command
    # never touches planner_state/itinerary_state, so the approved plan is
    # never regenerated or re-planned by a date-only update.
    logger.info(
        "Updated booking-date context for a frozen trip.",
        event="be.trip.booking_dates.updated",
        source="application",
        trip_id=trip_id,
        previous_precision=previous_precision,
        new_precision=new_precision,
        source_surface="dashboard_bookings",
        itinerary_regeneration_skipped=True,
    )
    return {"message": None, "agent_meta": None}
