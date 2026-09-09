"""Atlas input and rich final-itinerary contracts."""

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..trust_boundary import validate_phase_state
from .common import AgentMeta
from .trip_context import TripContext


AtlasText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
VerificationStatus = Literal["VERIFIED", "GENERAL_GUIDANCE"]
TimelineKind = Literal["TRAVEL", "STAY", "MEAL", "ACTIVITY", "FREE_TIME"]
# TWM-217: `dates` and `traveler_count` are gone — a day-numbered plan is
# always dateless (dates come from the composed trip dates / per-entity
# search prefs, never an Atlas guess) and a free-form count is represented
# by `summary.travelers.source` downstream, not an assumption.
AtlasAssumptionCategory = Literal[
    "arrival_departure_window",
    "stay_area",
    "budget",
    "other",
]
# Atlas never sees a real reservation, so "confirmed" is deliberately absent —
# TWM never holds or verifies a booking at all. TWM-217: `unresolved` is gone
# — an item whose booking status is merely uncertain carries
# `needs_verification: true` on its note instead.
AtlasBookingReadiness = Literal["suggested", "needs_advance_booking"]
# TWM-226: which trip endpoint a candidate gateway hub serves. `origin` for a
# hub the traveler passes through to *leave* a hubless origin town, `destination`
# for a hub they pass through to *reach* a hubless destination town.
AtlasHubSide = Literal["origin", "destination"]


class AtlasTransportHub(BaseModel):
    """TWM-226: a plain geographic fact about a candidate gateway city for a
    `TRAVEL` leg whose own endpoint town has no realistic long-haul transport.
    Atlas supplies an unranked plausible set (2-3); deterministic downstream
    feasibility/booking resolves modes, fares, and the final pick. Atlas never
    names a transit mode here -- `long_haul_distance_km` lets a rail-only hub
    with no airport still be assessed downstream."""

    model_config = ConfigDict(extra="forbid")

    city: AtlasText
    side: AtlasHubSide
    # All three are positive: a gateway sits a real surface transfer from the
    # town and a real long-haul distance from the trip's other endpoint, so a
    # zero on any of them is a degenerate "hub" that is not a hub.
    last_mile_km: int = Field(gt=0)
    last_mile_duration_minutes: int = Field(gt=0)
    long_haul_distance_km: int = Field(gt=0)


class AtlasAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AtlasAssumptionCategory
    detail: AtlasText


class AtlasReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus
    source_title: Optional[AtlasText] = None
    source_url: Optional[AtlasText] = None

    @model_validator(mode="after")
    def require_source_for_verified_detail(self) -> "AtlasReference":
        if self.status == "VERIFIED" and not (
            self.source_title and self.source_url
        ):
            raise ValueError("VERIFIED details require source_title and source_url")
        return self


class AtlasWorkingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1)
    date: Optional[AtlasText] = None
    places: list[AtlasText] = Field(default_factory=list)


class AtlasWorkingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destinations: list[AtlasText]
    trip_duration: int = Field(ge=1, le=60)
    approved_places: list[AtlasText] = Field(default_factory=list)
    days: list[AtlasWorkingDay]

    @model_validator(mode="after")
    def validate_approved_plan(self) -> "AtlasWorkingPlan":
        if len(self.days) != self.trip_duration:
            raise ValueError("working plan day count must equal trip_duration")
        if [day.day_number for day in self.days] != list(
            range(1, self.trip_duration + 1)
        ):
            raise ValueError("working plan days must be sequential from 1")
        allocated = [place for day in self.days for place in day.places]
        normalized = [place.casefold() for place in allocated]
        if len(normalized) != len(set(normalized)):
            raise ValueError("each approved place must be allocated exactly once")
        if self.approved_places and set(normalized) != {
            place.casefold() for place in self.approved_places
        }:
            raise ValueError("days must allocate every approved place and no others")
        return self


class AtlasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext = Field(default_factory=TripContext)
    working_plan: AtlasWorkingPlan

    @model_validator(mode="after")
    def validate_request(self) -> "AtlasRequest":
        validate_phase_state({"trip_context": self.trip_context.model_dump(mode="json")})
        return self


class AtlasTripSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: AtlasText
    destinations: list[AtlasText]
    trip_duration: int = Field(ge=1)
    num_travelers: Optional[int] = Field(default=None, ge=1)
    overview: AtlasText
    route_rationale: AtlasText


class AtlasTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: Optional[AtlasText] = None
    end_time: Optional[AtlasText] = None
    kind: TimelineKind
    title: AtlasText
    location: AtlasText
    detail: AtlasText
    movement_guidance: Optional[AtlasText] = None
    from_city: Optional[AtlasText] = None
    to_city: Optional[AtlasText] = None
    estimated_cost_low: Optional[int] = Field(default=None, ge=0)
    estimated_cost_high: Optional[int] = Field(default=None, ge=0)
    reference: AtlasReference
    requires_advance_booking: bool = False
    booking_readiness: Optional[AtlasBookingReadiness] = None
    # TWM-226: candidate gateway hubs for a TRAVEL leg whose own endpoint town
    # has no realistic long-haul transport -- presented as equal options (the
    # more commonly-used gateway first where there is one). Absent for a
    # normally-connected leg and when Atlas cannot confidently name a hub.
    hubs: Optional[list[AtlasTransportHub]] = None

    @model_validator(mode="after")
    def validate_cost_range(self) -> "AtlasTimelineItem":
        _validate_optional_range(self.estimated_cost_low, self.estimated_cost_high)
        return self

    @model_validator(mode="after")
    def validate_booking_readiness(self) -> "AtlasTimelineItem":
        if self.requires_advance_booking and self.booking_readiness is None:
            raise ValueError(
                "booking_readiness is required when requires_advance_booking is true"
            )
        if not self.requires_advance_booking and self.booking_readiness is not None:
            raise ValueError(
                "booking_readiness is allowed only when requires_advance_booking is true"
            )
        return self

    @model_validator(mode="after")
    def validate_movement_endpoints(self) -> "AtlasTimelineItem":
        if self.kind != "TRAVEL":
            if self.from_city is not None or self.to_city is not None:
                raise ValueError(
                    "from_city/to_city are allowed only when kind is TRAVEL"
                )
        if (self.from_city is None) != (self.to_city is None):
            raise ValueError(
                "from_city and to_city must both be present or both be absent"
            )
        return self

    @model_validator(mode="after")
    def validate_hubs(self) -> "AtlasTimelineItem":
        if self.hubs is None:
            return self
        if self.kind != "TRAVEL":
            raise ValueError("hubs are allowed only when kind is TRAVEL")
        if not self.hubs:
            raise ValueError(
                "hubs must be absent rather than an empty list when Atlas "
                "cannot confidently name a gateway hub"
            )
        return self


StayTier = Literal["budget", "mid_range", "premium"]
_STAY_TIER_ORDER: tuple[StayTier, ...] = ("budget", "mid_range", "premium")


class AtlasStayTierEstimate(BaseModel):
    """A single tier of TWM-204's non-binding stay price-band estimate --
    never a live/booked price (that stays structurally forbidden on
    TrustedAction), the same estimate-then-redirect honesty framing already
    applied to transit `estimated_cost_low/high`."""

    model_config = ConfigDict(extra="forbid")

    tier: StayTier
    estimated_cost_low: int = Field(ge=0)
    estimated_cost_high: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "AtlasStayTierEstimate":
        _validate_optional_range(self.estimated_cost_low, self.estimated_cost_high)
        return self


class AtlasDayNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AtlasText
    title: AtlasText
    detail: AtlasText
    reference: AtlasReference
    # TWM-217: "worth checking closer to travel; no live source confirmed
    # it." A day-specific verification gap lives here (not a separate list).
    needs_verification: bool = False


class AtlasDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1)
    title: AtlasText
    primary_location: AtlasText
    summary: AtlasText
    timeline: list[AtlasTimelineItem] = Field(min_length=1)
    notes: list[AtlasDayNote] = Field(default_factory=list)
    backup_plan: Optional[AtlasText] = None
    # TWM-204: present only when this day involves an overnight stay --
    # Atlas's own judgment call, the same as backup_plan above. Absent for
    # a day-trip/pure-transit/departure day with no overnight stay.
    stay_price_estimate: Optional[list[AtlasStayTierEstimate]] = None

    @model_validator(mode="after")
    def validate_stay_price_estimate(self) -> "AtlasDay":
        if self.stay_price_estimate is None:
            return self
        tiers = [entry.tier for entry in self.stay_price_estimate]
        if tiers != list(_STAY_TIER_ORDER):
            raise ValueError(
                "stay_price_estimate must contain exactly budget, mid_range, "
                "premium tiers in that order"
            )
        for previous, current in zip(self.stay_price_estimate, self.stay_price_estimate[1:]):
            if current.estimated_cost_low < previous.estimated_cost_low:
                raise ValueError(
                    "stay_price_estimate tiers must have non-decreasing "
                    "estimated_cost_low across budget -> mid_range -> premium"
                )
        return self


class AtlasBudgetLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AtlasText
    amount_low: int = Field(ge=0)
    amount_high: int = Field(ge=0)
    note: AtlasText

    @model_validator(mode="after")
    def validate_range(self) -> "AtlasBudgetLine":
        if self.amount_high < self.amount_low:
            raise ValueError("budget amount_high must be at least amount_low")
        return self


class AtlasBudgetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    currency: AtlasText
    lines: list[AtlasBudgetLine] = Field(min_length=1)
    total_low: int = Field(default=0, ge=0)
    total_high: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def calculate_totals(self) -> "AtlasBudgetSummary":
        self.total_low = sum(line.amount_low for line in self.lines)
        self.total_high = sum(line.amount_high for line in self.lines)
        return self


class AtlasPracticalNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AtlasText
    title: AtlasText
    detail: AtlasText
    reference: AtlasReference
    # TWM-217: a trip-wide verification gap lives here (not a separate list).
    needs_verification: bool = False


class AtlasSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: AtlasText
    url: AtlasText
    supports: list[AtlasText] = Field(min_length=1)


class AtlasFinalItinerary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_summary: AtlasTripSummary
    days: list[AtlasDay]
    budget_summary: AtlasBudgetSummary
    practical_notes: list[AtlasPracticalNote]
    sources: list[AtlasSource]
    assumptions: list[AtlasAssumption] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_days(self) -> "AtlasFinalItinerary":
        expected = self.trip_summary.trip_duration
        if len(self.days) != expected:
            raise ValueError("final itinerary day count must equal trip_duration")
        if [day.day_number for day in self.days] != list(range(1, expected + 1)):
            raise ValueError("final itinerary days must be sequential from 1")
        return self


class AtlasAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_itinerary: AtlasFinalItinerary


class AtlasResponse(AtlasAgentOutput):
    agent_meta: AgentMeta


def _validate_optional_range(low: Optional[int], high: Optional[int]) -> None:
    if (low is None) != (high is None):
        raise ValueError("cost range requires both low and high or neither")
    if low is not None and high is not None and high < low:
        raise ValueError("cost high must be at least cost low")
