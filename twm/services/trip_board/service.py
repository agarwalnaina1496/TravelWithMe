"""Trip Board composition (TWM-202).

Merges an Atlas final_itinerary with Trusted Actions feasibility for the
itinerary's two gateway TRAVEL legs (TWM-195/V1 scope: internal/circuit
legs are real itinerary content but never a bookable Trip Board row), and
reconciles trip_context.booking_dates (TWM-201, trip-wide, gateway-legs-
only) with each item's own Atlas-supplied departure_date/departure_month
into one date-precision signal per item.

Presentation only: every field traces to Atlas or Trusted Actions as-is.
This module never parses Atlas prose, never guesses a mode, and never
invents a date neither source gave it — see twm/schemas/trip_board.py's
module docstring for the same rule stated as a contract.
"""

import logging
from typing import Any, Optional
from uuid import UUID

from ...schemas.trip_board import TripBoardDay, TripBoardItem, TripBoardResponse
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


class TripBoardService:
    def __init__(self, trusted_action: TrustedActionService) -> None:
        self._trusted_action = trusted_action

    def build(
        self,
        trip_id: UUID,
        version: int,
        final_itinerary: dict[str, Any],
        trip_context: dict[str, Any],
    ) -> TripBoardResponse:
        origin_city = trip_context.get("origin_city")
        booking_dates = trip_context.get("booking_dates")

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
            board_items = [
                self._build_item(item, index, day["day_number"], outbound, inbound, booking_dates, trip_id)
                for index, item in enumerate(day.get("timeline", []))
            ]
            board_days.append(
                TripBoardDay(
                    day_number=day["day_number"],
                    title=day["title"],
                    primary_location=day["primary_location"],
                    summary=day["summary"],
                    seasonal_guidance=day["seasonal_guidance"],
                    permit_or_ticket_guidance=day["permit_or_ticket_guidance"],
                    backup_plan=day.get("backup_plan"),
                    items=board_items,
                )
            )
        return TripBoardResponse(version=version, days=board_days)

    def _build_item(
        self,
        item: dict[str, Any],
        index: int,
        day_number: int,
        outbound: Optional[dict[str, Any]],
        inbound: Optional[dict[str, Any]],
        booking_dates: Optional[dict[str, Any]],
        trip_id: UUID,
    ) -> TripBoardItem:
        is_travel_leg = item.get("kind") == "TRAVEL" and item.get("from_city") and item.get("to_city")
        is_outbound = is_travel_leg and item is outbound
        is_inbound = is_travel_leg and item is inbound
        is_gateway_leg = bool(is_outbound or is_inbound)

        date_precision = None
        departure_date = item.get("departure_date")
        departure_month = item.get("departure_month")

        if is_travel_leg:
            # PR review, TWM-202: guard each override branch on the actual
            # date field being present, not just on precision matching —
            # booking_dates' return_date is documented optional even under
            # "exact" precision (only departure_date confirmed yet), so
            # precision alone can't be trusted to mean "this leg has a
            # date". Reporting "exact"/"month" with a null date underneath
            # would misrepresent the leg worse than "flexible" would.
            override_date = None
            override_month = None
            if booking_dates and booking_dates.get("precision") == "exact" and (is_outbound or is_inbound):
                # Mirrors bookingCatalog.js's transportLegs — an exact
                # override only ever applies to the gateway leg matching its
                # own direction (outbound leg gets departure_date, inbound
                # leg gets return_date treated as *its own* departure date).
                override_date = (
                    booking_dates.get("departure_date") if is_outbound else booking_dates.get("return_date")
                )
            elif booking_dates and booking_dates.get("precision") == "month":
                # Month precision has no gateway restriction (TWM-201/
                # TWM-202 parity with transportLegs) — it applies to any
                # dateless TRAVEL leg, gateway or internal.
                override_month = booking_dates.get("departure_month")

            if departure_date:
                date_precision = "exact"
            elif departure_month:
                date_precision = "month"
            elif override_date:
                date_precision = "exact"
                departure_date = override_date
            elif override_month:
                date_precision = "month"
                departure_month = override_month
            else:
                date_precision = "flexible"

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
            id=f"{trip_id}:{day_number}:{index}",
            kind=item["kind"],
            title=item["title"],
            location=item["location"],
            detail=item["detail"],
            estimated_cost_low=item.get("estimated_cost_low"),
            estimated_cost_high=item.get("estimated_cost_high"),
            reference=item["reference"],
            requires_advance_booking=item.get("requires_advance_booking", False),
            booking_readiness=item.get("booking_readiness"),
            from_city=item.get("from_city"),
            to_city=item.get("to_city"),
            is_gateway_leg=is_gateway_leg,
            feasible_modes=feasible_modes,
            date_precision=date_precision,
            departure_date=departure_date,
            departure_month=departure_month,
        )
