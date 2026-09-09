"""Enrich a stored Atlas itinerary for ``GET /trips/{id}/itinerary`` (TWM-217).

The identity / date-resolution / stay-grouping logic relocated verbatim from
the deleted ``TripBoardService`` — minus feasibility (``feasible_modes`` is a
per-leg ``/trusted-action/feasibility`` call now).

Every timeline item gains a stable ``id``, ``is_gateway_leg``, and a resolved
date.

TWM-215: the trip's first and last ``TRAVEL`` legs are the booking-relevant
gateway legs, regardless of whether a stored ``origin_city`` string matches
either endpoint (split-origin / meeting-point trips, or an unset origin).
A gateway leg that carries Atlas ``hubs`` (TWM-226 — a hubless endpoint) has
each hub enriched with deterministic ``feasible_modes`` for its long-haul
portion; the indicative fare stays lazy (TWM-229).

Date resolution, in order:

    a per-entity ``search_prefs`` entry            -> date_source "search_pref"
    -> the item's itinerary-day calendar date,     -> date_source "trip_dates"
       which exists only when the composed trip
       dates are precision "exact" (day K = departure + K-1)
    -> none                                        -> date_source "none"

Presentation only: never parses prose, never invents a date no source gave.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional
from uuid import UUID

from ...telemetry import TelemetryLogger
from ..airport_resolution import resolve_airport
from ..trusted_action.feasibility import assess_trip_feasibility
from .trip_dates import TripDates

logger = logging.getLogger(__name__)

DateSource = str  # "search_pref" | "trip_dates" | "none"


def _search_pref(prefs: dict[str, Any], bucket: str, target_id: str) -> Optional[dict[str, Any]]:
    entry = (prefs.get(bucket) or {}).get(target_id) if isinstance(prefs, dict) else None
    return entry if isinstance(entry, dict) else None


def _segment_slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    return "-".join(part for part in slug.split("-") if part) or "stay"


def _day_calendar_date(trip_dates: TripDates, day_number: int) -> Optional[str]:
    if trip_dates.precision != "exact" or not trip_dates.departure:
        return None
    return (date.fromisoformat(trip_dates.departure) + timedelta(days=day_number - 1)).isoformat()


def enrich_itinerary(
    trip_id: UUID,
    final_itinerary: dict[str, Any],
    trip_context: dict[str, Any],
    booking_setup: dict[str, Any],
    trip_dates: TripDates,
    logger: TelemetryLogger,
) -> dict[str, Any]:
    search_prefs = (booking_setup or {}).get("search_prefs") or {}
    days = final_itinerary.get("days", [])

    travel_legs = [
        item
        for day in days
        for item in day.get("timeline", [])
        if item.get("kind") == "TRAVEL" and item.get("from_city") and item.get("to_city")
    ]
    # TWM-215: origin-agnostic — the trip's first and last movements are the
    # gateway legs, whatever cities they actually connect. A single-leg trip's
    # one leg is both. This assumes the first/last inter-city TRAVEL item *is*
    # the entry/exit leg, which TWM-226 guarantees for every itinerary Atlas
    # now generates (an explicit entry + exit leg always emitted). A pre-226
    # stored itinerary with no day-1 travel leg would flag its first internal
    # hop instead — acceptable pre-MVP; there is no legacy itinerary to migrate.
    outbound = travel_legs[0] if travel_legs else None
    inbound = travel_legs[-1] if travel_legs else None

    enriched_days = []
    for day in days:
        day_number = day["day_number"]
        calendar_date = _day_calendar_date(trip_dates, day_number)
        enriched_timeline = [
            _attach_hub_feasibility(
                trip_id,
                _enrich_item(item, index, day_number, calendar_date, outbound, inbound, search_prefs, trip_id),
                logger,
            )
            for index, item in enumerate(day.get("timeline", []))
        ]
        enriched_days.append({**day, "timeline": enriched_timeline})

    return {
        **final_itinerary,
        "days": enriched_days,
        "stay_segments": _build_stay_segments(trip_id, enriched_days, trip_dates, search_prefs),
    }


def _hub_long_haul_endpoints(
    from_city: str, to_city: str, from_hubless: bool, to_hubless: bool, hub: dict[str, Any]
) -> tuple[str, str]:
    """The (origin, destination) pair the hub's long-haul portion spans.

    Trust Atlas's ``side`` only as a tie-break: prefer replacing whichever
    leg endpoint the bundled resolver cannot actually place (TWM-226 PR #162
    review — ``side`` is Atlas's judgement and the schema cannot check it).
    ``from_hubless`` / ``to_hubless`` are the same for every hub on a leg, so
    the caller resolves them once rather than per hub.
    """
    if to_hubless and not from_hubless:
        return from_city, hub["city"]
    if from_hubless and not to_hubless:
        return hub["city"], to_city
    if hub.get("side") == "origin":
        return hub["city"], to_city
    return from_city, hub["city"]


def _attach_hub_feasibility(
    trip_id: UUID, item: dict[str, Any], logger: TelemetryLogger
) -> dict[str, Any]:
    hubs = item.get("hubs")
    if not (item.get("is_gateway_leg") and hubs):
        return item

    from_city, to_city = item["from_city"], item["to_city"]
    from_hubless = resolve_airport(from_city) is None
    to_hubless = resolve_airport(to_city) is None
    resolved_hubs: list[dict[str, Any]] = []
    fallback_count = 0
    unresolved: list[str] = []
    for hub in hubs:
        origin, destination = _hub_long_haul_endpoints(
            from_city, to_city, from_hubless, to_hubless, hub
        )
        assessment = assess_trip_feasibility(origin, destination, hub.get("long_haul_distance_km"))
        modes = [entry.mode for entry in assessment.modes]
        if resolve_airport(hub.get("city", "")) is None:
            fallback_count += 1
        if not modes:
            unresolved.append(hub.get("city", ""))
        resolved_hubs.append({**hub, "feasible_modes": modes})

    _log_hub_resolution(trip_id, item, len(hubs), fallback_count, unresolved, logger)
    return {**item, "hubs": resolved_hubs}


def _log_hub_resolution(
    trip_id: UUID,
    item: dict[str, Any],
    candidate_count: int,
    fallback_count: int,
    unresolved: list[str],
    logger: TelemetryLogger,
) -> None:
    fields = {
        "event": "be.itinerary.hub_resolution",
        "source": "application",
        "trip_id": str(trip_id),
        "leg_id": item["id"],
        "from_city": item.get("from_city"),
        "to_city": item.get("to_city"),
        "candidate_hub_count": candidate_count,
        "resolved_hub_count": candidate_count - len(unresolved),
        "distance_fallback_count": fallback_count,
    }
    if unresolved:
        logger.warning(
            "Gateway leg has candidate hubs that resolved to no feasible transport modes.",
            unresolved_hubs=unresolved,
            **fields,
        )
    else:
        logger.info("Resolved candidate gateway hubs for a gateway leg.", **fields)


def _enrich_item(
    item: dict[str, Any],
    index: int,
    day_number: int,
    calendar_date: Optional[str],
    outbound: Optional[dict[str, Any]],
    inbound: Optional[dict[str, Any]],
    search_prefs: dict[str, Any],
    trip_id: UUID,
) -> dict[str, Any]:
    is_travel_leg = item.get("kind") == "TRAVEL" and item.get("from_city") and item.get("to_city")
    is_gateway_leg = bool(is_travel_leg and (item is outbound or item is inbound))
    item_id = f"{trip_id}:{day_number}:{index}"

    resolved_date: Optional[str] = None
    date_precision = "none"
    date_source = "none"

    if item.get("kind") in ("TRAVEL", "STAY"):
        bucket = "transports" if item.get("kind") == "TRAVEL" else "stays"
        target_id = item_id if item.get("kind") == "TRAVEL" else _stay_item_id(item, trip_id, day_number, index)
        override = _search_pref(search_prefs, bucket, target_id)
        if override and override.get("precision") == "exact" and override.get("date"):
            resolved_date, date_precision, date_source = override["date"], "exact", "search_pref"
        elif override and override.get("precision") == "month" and override.get("month"):
            resolved_date, date_precision, date_source = override["month"], "month", "search_pref"
        elif calendar_date is not None:
            resolved_date, date_precision, date_source = calendar_date, "exact", "trip_dates"

    return {
        **item,
        "id": item_id,
        "is_gateway_leg": is_gateway_leg,
        "resolved_date": resolved_date,
        "date_precision": date_precision,
        "date_source": date_source,
    }


def _stay_item_id(item: dict[str, Any], trip_id: UUID, day_number: int, index: int) -> str:
    # A STAY timeline item's search pref is keyed by its board item id — the
    # same {trip_id}:{day}:{index} identity as any other item.
    return f"{trip_id}:{day_number}:{index}"


def _build_stay_segments(
    trip_id: UUID,
    enriched_days: list[dict[str, Any]],
    trip_dates: TripDates,
    search_prefs: dict[str, Any],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current
        if current:
            segments.append(_finish_segment(trip_id, current, trip_dates, search_prefs))
            current = None

    for day in enriched_days:
        for item in (i for i in day["timeline"] if i.get("kind") == "STAY"):
            location = (item.get("location") or "").strip()
            if not location:
                finish()
                continue
            if (
                current
                and current["location"].casefold() == location.casefold()
                and day["day_number"] == current["end_day_number"] + 1
            ):
                current["end_day_number"] = day["day_number"]
                current["board_item_ids"].append(item["id"])
                continue
            finish()
            current = {
                "location": location,
                "start_day_number": day["day_number"],
                "end_day_number": day["day_number"],
                "board_item_ids": [item["id"]],
            }
    finish()
    return segments


def _finish_segment(
    trip_id: UUID,
    segment: dict[str, Any],
    trip_dates: TripDates,
    search_prefs: dict[str, Any],
) -> dict[str, Any]:
    start_day, end_day = segment["start_day_number"], segment["end_day_number"]
    nights = end_day - start_day + 1
    slug = _segment_slug(segment["location"])
    segment_id = f"{trip_id}:stay:{start_day}:{end_day}:{slug}"
    override = _search_pref(search_prefs, "stays", segment_id)

    checkin_date: Optional[str] = None
    checkout_date: Optional[str] = None
    month: Optional[str] = None

    if override and override.get("precision") == "exact" and override.get("date"):
        checkin = date.fromisoformat(override["date"])
        checkin_date, checkout_date = checkin.isoformat(), (checkin + timedelta(days=nights)).isoformat()
        date_precision, date_source = "exact", "search_pref"
    elif override and override.get("precision") == "month" and override.get("month"):
        month, date_precision, date_source = override["month"], "month", "search_pref"
    elif (day_date := _day_calendar_date(trip_dates, start_day)) is not None:
        checkin = date.fromisoformat(day_date)
        checkin_date, checkout_date = checkin.isoformat(), (checkin + timedelta(days=nights)).isoformat()
        date_precision, date_source = "exact", "trip_dates"
    else:
        date_precision, date_source = "none", "none"

    return {
        "id": segment_id,
        "location": segment["location"],
        "start_day_number": start_day,
        "end_day_number": end_day,
        "nights": nights,
        "date_precision": date_precision,
        "checkin_date": checkin_date,
        "checkout_date": checkout_date,
        "month": month,
        "date_source": date_source,
        "board_item_ids": segment["board_item_ids"],
    }
