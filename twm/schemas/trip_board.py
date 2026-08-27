"""Trip Board (TWM-202/TWM-212): a lean itinerary feasibility overlay.

Presentation-only composition. The Board carries only stable item identity,
structured route endpoints, gateway feasibility, and reconciled date
precision. Atlas remains the source for itinerary content.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .atlas import TimelineKind
from .trusted_action import ModeFeasibility

DatePrecision = Literal["exact", "month", "flexible"]


class TripBoardItem(BaseModel):
    """One lean itinerary row plus computed feasibility/date overlay."""

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
    items: list[TripBoardItem]


class TripBoardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    days: list[TripBoardDay]
