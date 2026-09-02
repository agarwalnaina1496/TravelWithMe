"""Trip Board composition (TWM-202/TWM-212/TWM-216).

Merges an Atlas final_itinerary with Trusted Actions feasibility for the
itinerary's two gateway TRAVEL legs (TWM-195/V1 scope: internal/circuit
legs are real itinerary content but never a bookable Trip Board row), and
resolves one effective date + precision per bookable entity from a single
uniform precedence:

    booking_setup.search_prefs override
      -> Atlas's own per-item departure_date/month  (TRAVEL only)
      -> derived from booking_setup.start + the item's day number
      -> flexible

booking_setup.start is the trip's calendar anchor: day ``K`` falls on
``start.date + (K - 1)``, so every timeline item and stay segment gets its
own real date once an exact start is known — no trip-wide/gateway-only
special-casing. A search pref whose target id no longer matches a live
Board entity is simply never applied (it is inert, not an error).

Presentation only: this module never parses Atlas prose, never guesses a
mode, and never invents a date no source gave it.
"""

import logging
from datetime import date, timedelta
from typing import Any, Optional
from uuid import UUID

from ...schemas.trip_board import (
    TripBoardDay,
    TripBoardItem,
    TripBoardResponse,
    TripBoardStaySegment,
)
from ..airport_resolution import resolve_airport
from ..trusted_action import TrustedActionService

logger = logging.getLogger(__name__)


def _city_matches(left: str, right: str) -> bool:
    if left.strip().casefold() == right.strip().casefold():
        return True

    left_match = resolve_airport(left)
    right_match = resolve_airport(right)
    if left_match is None or right_match is None:
        if left_match is None:
            logger.warning("Could not resolve trip-board city: %s", left)
        if right_match is None:
            logger.warning("Could not resolve trip-board city: %s", right)
        return False
    return left_match.iata == right_match.iata


def _search_pref(prefs: dict[str, Any], bucket: str, target_id: str) -> Optional[dict[str, Any]]:
    entry = (prefs.get(bucket) or {}).get(target_id) if isinstance(prefs, dict) else None
    return entry if isinstance(entry, dict) else None


class TripBoardService:
    def __init__(self, trusted_action: TrustedActionService) -> None:
        self._trusted_action = trusted_action

    def build(
        self,
        trip_id: UUID,
        version: int,
        final_itinerary: dict[str, Any],
        trip_context: dict[str, Any],
        booking_setup: Optional[dict[str, Any]] = None,
    ) -> TripBoardResponse:
        origin_city = trip_context.get("origin_city")
        booking_setup = booking_setup or {}
        start = booking_setup.get("start") if isinstance(booking_setup, dict) else None
        trip_start = self._exact_start_date(start)
        start_month = start.get("month") if isinstance(start, dict) and start.get("precision") == "month" else None
        search_prefs = booking_setup.get("search_prefs") or {}

        days = final_itinerary.get("days", [])
        travel_legs = [
            item
            for day in days
            for item in day.get("timeline", [])
            if item.get("kind") == "TRAVEL"
            and item.get("from_city")
            and item.get("to_city")
        ]
        outbound = next(
            (leg for leg in travel_legs if origin_city and _city_matches(leg["from_city"], origin_city)),
            None,
        )
        inbound = next(
            (leg for leg in reversed(travel_legs) if origin_city and _city_matches(leg["to_city"], origin_city)),
            None,
        )

        board_days = []
        for day in days:
            day_number = day["day_number"]
            calendar_date = (
                (trip_start + timedelta(days=day_number - 1)).isoformat()
                if trip_start is not None
                else None
            )
            if calendar_date is not None:
                logger.info(
                    "Computed calendar date for trip-board day.",
                    extra={
                        "event": "be.trip_board.day_date.computed",
                        "trip_id": str(trip_id),
                        "day_number": day_number,
                        "calendar_date": calendar_date,
                    },
                )
            board_items = [
                self._build_item(
                    item, index, day_number, calendar_date, start_month,
                    outbound, inbound, search_prefs, trip_id,
                )
                for index, item in enumerate(day.get("timeline", []))
            ]
            board_days.append(
                TripBoardDay(
                    day_number=day_number,
                    date=calendar_date,
                    items=board_items,
                )
            )
        return TripBoardResponse(
            version=version,
            days=board_days,
            stay_segments=self._build_stay_segments(trip_id, board_days, start_month, search_prefs),
        )

    @staticmethod
    def _build_stay_segments(
        trip_id: UUID,
        board_days: list[TripBoardDay],
        start_month: Optional[str],
        search_prefs: dict[str, Any],
    ) -> list[TripBoardStaySegment]:
        segments: list[TripBoardStaySegment] = []
        current: dict[str, Any] | None = None

        def finish() -> None:
            nonlocal current
            if current:
                segments.append(
                    TripBoardService._finish_stay_segment(trip_id, current, board_days, start_month, search_prefs)
                )
                current = None

        for day in board_days:
            for item in (item for item in day.items if item.kind == "STAY"):
                location = item.location.strip() if item.location else ""
                if not location:
                    finish()
                    continue
                if (
                    current
                    and current["location"].casefold() == location.casefold()
                    and day.day_number == current["end_day_number"] + 1
                ):
                    current["end_day_number"] = day.day_number
                    current["board_item_ids"].append(item.id)
                    continue
                finish()
                current = {
                    "location": location,
                    "start_day_number": day.day_number,
                    "end_day_number": day.day_number,
                    "board_item_ids": [item.id],
                }
        finish()
        return segments

    @staticmethod
    def _finish_stay_segment(
        trip_id: UUID,
        segment: dict[str, Any],
        board_days: list[TripBoardDay],
        start_month: Optional[str],
        search_prefs: dict[str, Any],
    ) -> TripBoardStaySegment:
        day_by_number = {day.day_number: day for day in board_days}
        start_day_number = segment["start_day_number"]
        end_day_number = segment["end_day_number"]
        nights = end_day_number - start_day_number + 1
        anchored_start = day_by_number.get(start_day_number)
        anchored_date = anchored_start.date if anchored_start else None

        slug = TripBoardService._segment_slug(segment["location"])
        segment_id = f"{trip_id}:stay:{start_day_number}:{end_day_number}:{slug}"
        override = _search_pref(search_prefs, "stays", segment_id)

        checkin_date: Optional[str] = None
        checkout_date: Optional[str] = None
        departure_month: Optional[str] = None
        if override and override.get("precision") == "exact" and override.get("date"):
            checkin = date.fromisoformat(override["date"])
            checkin_date = checkin.isoformat()
            checkout_date = (checkin + timedelta(days=nights)).isoformat()
            date_precision = "exact"
            date_source = "override"
        elif override and override.get("precision") == "month" and override.get("month"):
            departure_month = override["month"]
            date_precision = "month"
            date_source = "override"
        elif anchored_date:
            checkin = date.fromisoformat(anchored_date)
            checkin_date = checkin.isoformat()
            checkout_date = (checkin + timedelta(days=nights)).isoformat()
            date_precision = "exact"
            date_source = "anchor"
        elif start_month:
            departure_month = start_month
            date_precision = "month"
            date_source = "anchor"
        else:
            date_precision = "flexible"
            date_source = "none"

        return TripBoardStaySegment(
            id=segment_id,
            location=segment["location"],
            start_day_number=start_day_number,
            end_day_number=end_day_number,
            nights=nights,
            date_precision=date_precision,
            checkin_date=checkin_date,
            checkout_date=checkout_date,
            departure_month=departure_month,
            date_source=date_source,
            board_item_ids=segment["board_item_ids"],
        )

    @staticmethod
    def _segment_slug(value: str) -> str:
        slug = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
        return "-".join(part for part in slug.split("-") if part) or "stay"

    @staticmethod
    def _exact_start_date(start: Optional[dict[str, Any]]) -> Optional[date]:
        if not isinstance(start, dict) or start.get("precision") != "exact":
            return None
        value = start.get("date")
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except (TypeError, ValueError):
            logger.warning("Could not parse booking_setup.start date.")
            return None

    def _build_item(
        self,
        item: dict[str, Any],
        index: int,
        day_number: int,
        calendar_date: Optional[str],
        start_month: Optional[str],
        outbound: Optional[dict[str, Any]],
        inbound: Optional[dict[str, Any]],
        search_prefs: dict[str, Any],
        trip_id: UUID,
    ) -> TripBoardItem:
        is_travel_leg = item.get("kind") == "TRAVEL" and item.get("from_city") and item.get("to_city")
        is_gateway_leg = bool(is_travel_leg and (item is outbound or item is inbound))
        item_id = f"{trip_id}:{day_number}:{index}"

        date_precision = None
        departure_date = None
        departure_month = None
        date_source = None

        if is_travel_leg:
            atlas_date = item.get("departure_date")
            atlas_month = item.get("departure_month")
            override = _search_pref(search_prefs, "transports", item_id)
            if atlas_date:
                departure_date, date_precision, date_source = atlas_date, "exact", "itinerary"
            elif atlas_month:
                departure_month, date_precision, date_source = atlas_month, "month", "itinerary"
            elif override and override.get("precision") == "exact" and override.get("date"):
                departure_date, date_precision, date_source = override["date"], "exact", "override"
            elif override and override.get("precision") == "month" and override.get("month"):
                departure_month, date_precision, date_source = override["month"], "month", "override"
            elif calendar_date is not None:
                # Every leg happens on its own itinerary day, so the anchor
                # gives it that day's real date — gateway or internal alike.
                departure_date, date_precision, date_source = calendar_date, "exact", "anchor"
            elif start_month:
                departure_month, date_precision, date_source = start_month, "month", "anchor"
            else:
                date_precision, date_source = "flexible", "none"

        feasible_modes = None
        if is_gateway_leg:
            assessment = self._trusted_action.assess_feasibility(
                trip_id, item["from_city"], item["to_city"]
            )
            feasible_modes = assessment.modes

        return TripBoardItem(
            # TWM-209: deterministic across two build() calls for the same
            # itinerary version (day_number + timeline index never change
            # for the same days list), and distinct per item within a day
            # and across days.
            id=item_id,
            kind=item["kind"],
            location=item.get("location"),
            from_city=item.get("from_city"),
            to_city=item.get("to_city"),
            is_gateway_leg=is_gateway_leg,
            feasible_modes=feasible_modes,
            date_precision=date_precision,
            departure_date=departure_date,
            departure_month=departure_month,
            date_source=date_source,
        )
