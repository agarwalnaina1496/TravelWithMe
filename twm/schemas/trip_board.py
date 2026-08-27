"""Trip Board (TWM-202): one composed itinerary/booking item list shared by
Overview and Itinerary, instead of each screen deriving its own view of
Atlas + Trusted Actions data independently.

Presentation-only composition. Every field here traces to Atlas or Trusted
Actions feasibility as-is; this module reshapes/groups/computes date
precision from numbers and flags those sources already gave it, and never
parses, corrects, or guesses at a fact neither source provided. See
``twm/services/trip_board/service.py`` for the composition itself.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .atlas import AtlasReference, AtlasBookingReadiness, TimelineKind
from .trusted_action import ModeFeasibility

DatePrecision = Literal["exact", "month", "flexible"]


class TripBoardItem(BaseModel):
    """One enriched itinerary item — an Atlas timeline entry, plus (for a
    gateway TRAVEL leg) Trusted Actions feasibility once computed."""

    model_config = ConfigDict(extra="forbid")

    # TWM-209: a stable, deterministic identity for this item — derived from
    # trip_id + day number + timeline index, so it stays consistent across
    # requests for the same itinerary version without needing Atlas to
    # supply one. Lets a consumer (e.g. UI readiness/anchor matching) match
    # against the exact item rather than falling back to a fragile day/type
    # match. Not stable across an itinerary *revision* that reorders/adds/
    # removes timeline items — callers matching across versions still need
    # their own reconciliation for that case.
    id: str
    kind: TimelineKind
    title: str
    location: str
    detail: str
    estimated_cost_low: Optional[int] = None
    estimated_cost_high: Optional[int] = None
    reference: AtlasReference
    requires_advance_booking: bool = False
    booking_readiness: Optional[AtlasBookingReadiness] = None

    # TRAVEL-only, passed through verbatim from Atlas.
    from_city: Optional[str] = None
    to_city: Optional[str] = None

    # TWM-195/V1 scope: only the two gateway legs (the itinerary's boundary
    # movements to/from trip_context.origin_city) are ever assessed for
    # feasibility — an internal/circuit leg is real itinerary content but
    # never a bookable Trip Board row. False for every non-TRAVEL item too.
    is_gateway_leg: bool = False

    # None means "not yet computed" (never assessed, e.g. a non-gateway
    # TRAVEL leg) — deliberately distinct from an empty list, which means
    # Trusted Actions assessed the route and found zero feasible modes.
    # The adapter never guesses one for the other.
    feasible_modes: Optional[list[ModeFeasibility]] = None

    # Reconciles TripBookingDateInput (trip-wide, gateway-legs-only),
    # Atlas's own per-item departure_date/departure_month, and the concept
    # of "no date known at all" into one signal every consumer can trust,
    # instead of each screen re-deriving precision from a different
    # upstream shape. Always present for a TRAVEL item; None for every
    # other kind.
    date_precision: Optional[DatePrecision] = None
    departure_date: Optional[str] = None
    departure_month: Optional[str] = None


class TripBoardDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int
    title: str
    primary_location: str
    summary: str
    seasonal_guidance: str
    permit_or_ticket_guidance: str
    backup_plan: Optional[str] = None
    items: list[TripBoardItem]


class TripBoardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    days: list[TripBoardDay]
