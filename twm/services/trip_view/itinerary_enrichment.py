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
_LONG_JOURNEY_AVG_SPEED_KMH = 45.0
_LONG_JOURNEY_THRESHOLD_HOURS = 20
_LONG_JOURNEY_ROUND_TO_HOURS = 6

# Drive is never bookable on a gateway leg. It is surfaced in transport_options
# only so the chooser can explain the absence; when the distance rules would
# otherwise call a short gateway leg drive-feasible, this is the honest line
# (the "too far" reason is kept whenever it actually applies).
_DRIVE_NOT_BOOKED_REASON = (
    "TWM books flights and trains for gateway legs — arrange a drive yourself."
)


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
                _itinerary_locations(days),
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


def _itinerary_locations(days: list[dict[str, Any]]) -> set[str]:
    return {
        str(day.get("primary_location", "")).strip().casefold()
        for day in days
        if str(day.get("primary_location", "")).strip()
    }


def _attach_hub_feasibility(
    trip_id: UUID, item: dict[str, Any], itinerary_locations: set[str], logger: TelemetryLogger
) -> dict[str, Any]:
    hubs = item.get("hubs")
    if not item.get("is_gateway_leg"):
        return item

    from_city, to_city = item["from_city"], item["to_city"]
    from_hubless = resolve_airport(from_city) is None
    to_hubless = resolve_airport(to_city) is None
    suppressed_hubs = []
    candidate_hubs = []
    for hub in hubs or []:
        if str(hub.get("city", "")).strip().casefold() in itinerary_locations:
            suppressed_hubs.append(hub)
            continue
        candidate_hubs.append(hub)
    suppressed_count = len(hubs or []) - len(candidate_hubs)
    requested_access_gaps = {hub.get("access_gap") for hub in candidate_hubs}
    suppressed_access_gaps = {hub.get("access_gap") for hub in suppressed_hubs}
    transport_options = _resolve_transport_options(
        from_city, to_city, from_hubless, to_hubless, candidate_hubs,
        requested_access_gaps, suppressed_access_gaps
    )
    _log_hub_resolution(
        trip_id, item, len(hubs or []), transport_options, suppressed_count, logger
    )
    item = {key: value for key, value in item.items() if key != "hubs"}
    return {**item, "transport_options": transport_options}


def _assessment_by_mode(origin: str, destination: str, long_haul_distance_km: Any = None) -> dict[str, Any]:
    return {
        entry.mode: entry
        for entry in assess_trip_feasibility(origin, destination, long_haul_distance_km).modes
    }


def _rough_hub_distance(hubs: list[dict[str, Any]]) -> float | None:
    distances = [
        float(distance)
        for hub in hubs
        if (distance := hub.get("long_haul_distance_km")) is not None and distance > 0
    ]
    return max(distances, default=None)


def _long_journey_note(distance_km: Any) -> str | None:
    if distance_km is None:
        return None
    hours = float(distance_km) / _LONG_JOURNEY_AVG_SPEED_KMH
    if hours < _LONG_JOURNEY_THRESHOLD_HOURS:
        return None
    rounded_hours = int(round(hours / _LONG_JOURNEY_ROUND_TO_HOURS) * _LONG_JOURNEY_ROUND_TO_HOURS)
    rounded_hours = max(_LONG_JOURNEY_THRESHOLD_HOURS, rounded_hours)
    return f"Roughly {rounded_hours} h long-haul journey before the local transfer."


def _long_journey_note_for_mode(mode: str, hubs: list[dict[str, Any]]) -> str | None:
    if mode not in {"train", "bus"}:
        return None
    return _long_journey_note(
        max((hub.get("long_haul_distance_km") or 0 for hub in hubs), default=0) or None
    )


def _option_from_assessment(mode: str, entry: Any, *, direct: bool, hubs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": mode,
        "direct": direct,
        "feasible": entry.status == "feasible",
        "ruled_out_reason": entry.reason if entry.status != "feasible" else None,
        "long_journey_note": _long_journey_note_for_mode(mode, hubs),
        "hubs": hubs,
    }


def _first_not_feasible_reason(assessments: list[dict[str, Any]], mode: str) -> str | None:
    for assessment in assessments:
        entry = assessment.get(mode)
        if entry and entry.status != "feasible":
            return entry.reason
    return None


def _resolve_transport_options(
    from_city: str,
    to_city: str,
    from_hubless: bool,
    to_hubless: bool,
    hubs: list[dict[str, Any]],
    requested_access_gaps: set[Any] | None = None,
    suppressed_access_gaps: set[Any] | None = None,
) -> list[dict[str, Any]]:
    direct_assessment = _assessment_by_mode(from_city, to_city)
    rough_direct_assessment = _assessment_by_mode(from_city, to_city, _rough_hub_distance(hubs)) if hubs else {}
    options: list[dict[str, Any]] = []
    for mode in ("flight", "train", "bus", "drive"):
        # Drive is surfaced only so UI can explain why it is not bookable in
        # the chooser; it never maps to a trusted-action partner request.
        access_gap = "air" if mode == "flight" else "rail" if mode in ("train", "bus") else None
        mode_hubs = [hub for hub in hubs if hub.get("access_gap") == access_gap]
        if not mode_hubs:
            if suppressed_access_gaps and access_gap in suppressed_access_gaps:
                continue
            if mode == "drive":
                rough = rough_direct_assessment.get("drive") or direct_assessment["drive"]
                option = _option_from_assessment("drive", rough, direct=True, hubs=[])
                if option["feasible"]:
                    option = {**option, "feasible": False, "ruled_out_reason": _DRIVE_NOT_BOOKED_REASON}
                options.append(option)
                continue
            options.append(_option_from_assessment(mode, direct_assessment[mode], direct=True, hubs=[]))
            continue
        resolved_hubs: list[dict[str, Any]] = []
        hub_assessments = []
        for hub in mode_hubs:
            origin, destination = _hub_long_haul_endpoints(
                from_city, to_city, from_hubless, to_hubless, hub
            )
            assessment = _assessment_by_mode(origin, destination, hub.get("long_haul_distance_km"))
            hub_assessments.append(assessment)
            entry = assessment[mode]
            resolved_hubs.append({
                "city": hub.get("city"),
                "access_gap": hub.get("access_gap"),
                "side": hub.get("side"),
                "last_mile_km": hub.get("last_mile_km"),
                "last_mile_duration_minutes": hub.get("last_mile_duration_minutes"),
                "distance_km": hub.get("long_haul_distance_km"),
                "long_haul_distance_km": hub.get("long_haul_distance_km"),
                "feasible": entry.status == "feasible",
            })
        feasible = any(hub.get("feasible") for hub in resolved_hubs)
        options.append({
            "mode": mode,
            "direct": False,
            "feasible": feasible,
            "ruled_out_reason": None if feasible else _first_not_feasible_reason(hub_assessments, mode),
            "long_journey_note": _long_journey_note_for_mode(mode, mode_hubs),
            "hubs": resolved_hubs,
        })
    return options


def _log_hub_resolution(
    trip_id: UUID,
    item: dict[str, Any],
    candidate_count: int,
    transport_options: list[dict[str, Any]],
    suppressed_count: int,
    logger: TelemetryLogger,
) -> None:
    mode_counts = {
        option["mode"]: {
            "resolution": "direct" if option.get("direct") else "via_hub",
            "candidate_hub_count": len(option.get("hubs") or []),
            "feasible_hub_count": len([hub for hub in option.get("hubs") or [] if hub.get("feasible")]),
            "feasible_count": 1 if option.get("feasible") else 0,
            "not_feasible_count": 0 if option.get("feasible") else 1,
            "ruled_out_reasons": [option["ruled_out_reason"]] if option.get("ruled_out_reason") else [],
            "long_journey_note_present": bool(option.get("long_journey_note")),
        }
        for option in transport_options
    }
    unresolved_modes = [
        option["mode"]
        for option in transport_options
        if not option.get("direct") and not any(hub.get("feasible") for hub in option.get("hubs") or [])
    ]
    fields = {
        "event": "be.itinerary.hub_resolution",
        "source": "application",
        "trip_id": str(trip_id),
        "leg_id": item["id"],
        "from_city": item.get("from_city"),
        "to_city": item.get("to_city"),
        "candidate_hub_count": candidate_count,
        "suppressed_hub_count": suppressed_count,
        "rail_hub_count": sum(
            1
            for option in transport_options
            for hub in option.get("hubs") or []
            if hub.get("access_gap") == "rail"
        ),
        "mode_resolution_counts": mode_counts,
    }
    if unresolved_modes:
        logger.warning(
            "Gateway leg has per-mode transport resolutions with no feasible options.",
            unresolved_modes=unresolved_modes,
            **fields,
        )
    else:
        logger.info("Resolved per-mode gateway transport options for a gateway leg.", **fields)


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
        # A standard OTA form: the traveler can move check-out independently of
        # the plan's night count. Fall back to check-in + itinerary nights only
        # when they haven't set one.
        checkout = (
            date.fromisoformat(override["checkout_date"])
            if override.get("checkout_date")
            else checkin + timedelta(days=nights)
        )
        checkin_date, checkout_date = checkin.isoformat(), checkout.isoformat()
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
