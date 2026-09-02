"""``booking_setup`` command handlers (TWM-216).

Four deterministic, Backend-owned writes to the ``booking_setup`` state
branch — the calendar anchor, the structured party, and per-entity search
dates. None regenerates or re-plans the itinerary; all require a frozen
plan with an itinerary already generated (the surfaces that call them only
exist post-generation).
"""

from typing import Any

from ...schemas.booking_setup import (
    ScheduleDateInput,
    SearchPrefClearInput,
    SearchPrefInput,
    TravelerComposition,
)
from ...telemetry import TelemetryLogger
from .errors import InvalidTripCommandError


def _require_generated_itinerary(state: dict[str, Any]) -> None:
    """booking_setup facts only mean something once Atlas has produced an
    itinerary to hang dates and searches off. current_version existing also
    implies the plan was frozen (Atlas only ever runs against a frozen
    plan), so this one check covers both preconditions.
    """
    if not state.get("itinerary_state", {}).get("current_version"):
        raise InvalidTripCommandError(
            "booking_setup can only be updated once Atlas has produced an itinerary."
        )


def _booking_setup(state: dict[str, Any]) -> dict[str, Any]:
    branch = state.setdefault("booking_setup", {})
    if not isinstance(branch, dict):
        branch = {}
        state["booking_setup"] = branch
    return branch


def apply_set_trip_start(
    logger: TelemetryLogger,
    state: dict[str, Any],
    update: ScheduleDateInput,
) -> dict[str, Any]:
    _require_generated_itinerary(state)
    branch = _booking_setup(state)
    previous = branch.get("start")
    branch["start"] = update.as_stored()

    logger.info(
        "Updated the trip calendar anchor.",
        event="be.trip.booking_setup.start.updated",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        previous_precision=previous.get("precision") if isinstance(previous, dict) else None,
        new_precision=branch["start"]["precision"],
        itinerary_regeneration_skipped=True,
    )
    return {"message": None, "agent_meta": None}


def apply_set_party(
    logger: TelemetryLogger,
    state: dict[str, Any],
    update: TravelerComposition,
) -> dict[str, Any]:
    _require_generated_itinerary(state)
    branch = _booking_setup(state)
    previous = branch.get("party")
    branch["party"] = update.model_dump(mode="json")

    logger.info(
        "Updated the structured traveler party.",
        event="be.trip.booking_setup.party.updated",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        previous_total=(
            previous.get("adults", 0) + previous.get("children", 0) + previous.get("infants", 0)
            if isinstance(previous, dict)
            else None
        ),
        new_total=update.total,
    )
    return {"message": None, "agent_meta": None}


def apply_set_search_pref(
    logger: TelemetryLogger,
    state: dict[str, Any],
    update: SearchPrefInput,
) -> dict[str, Any]:
    _require_generated_itinerary(state)
    branch = _booking_setup(state)
    prefs = branch.setdefault("search_prefs", {})
    bucket = prefs.setdefault(f"{update.target_type}s", {})
    previous = bucket.get(update.target_id)
    bucket[update.target_id] = update.as_stored()

    logger.info(
        "Updated a booking-search date preference.",
        event="be.trip.booking_setup.search_pref.updated",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        target_type=update.target_type,
        target_id=update.target_id,
        previous_precision=previous.get("precision") if isinstance(previous, dict) else None,
        new_precision=bucket[update.target_id]["precision"],
        itinerary_regeneration_skipped=True,
    )
    return {"message": None, "agent_meta": None}


def apply_clear_search_pref(
    logger: TelemetryLogger,
    state: dict[str, Any],
    update: SearchPrefClearInput,
) -> dict[str, Any]:
    _require_generated_itinerary(state)
    branch = _booking_setup(state)
    bucket = branch.get("search_prefs", {}).get(f"{update.target_type}s", {})
    removed = bucket.pop(update.target_id, None)

    logger.info(
        "Cleared a booking-search date preference.",
        event="be.trip.booking_setup.search_pref.cleared",
        source="application",
        trip_id=str(state.get("trip_id")) if state.get("trip_id") else None,
        target_type=update.target_type,
        target_id=update.target_id,
        was_set=removed is not None,
        itinerary_regeneration_skipped=True,
    )
    return {"message": None, "agent_meta": None}
