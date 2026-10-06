"""Atlas input and rich final-itinerary contracts."""

from typing import Annotated, Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from ..trust_boundary import validate_phase_state
from .agent_contract import (
    AgentContent,
    EmptyAsNone,
    IntOrNone,
    LenientInt,
    NullAsEmptyList,
    OptionalText,
    Text,
    case_insensitive,
    ordered_range,
    number_by_position,
    record_heal,
)
from .common import AgentMeta
from .trip_context import TripContext


VerificationStatus = case_insensitive(Literal["VERIFIED", "GENERAL_GUIDANCE"])
TimelineKind = case_insensitive(Literal["TRAVEL", "STAY", "MEAL", "ACTIVITY", "FREE_TIME"])
# TWM-217: `dates` and `traveler_count` are gone — a day-numbered plan is
# always dateless (dates come from the composed trip dates / per-entity
# search prefs, never an Atlas guess) and a free-form count is represented
# by `summary.travelers.source` downstream, not an assumption.
AtlasAssumptionCategory = case_insensitive(
    Literal[
        "arrival_departure_window",
        "stay_area",
        "budget",
        "other",
    ]
)
# Atlas never sees a real reservation, so "confirmed" is deliberately absent —
# TWM never holds or verifies a booking at all. TWM-217: `unresolved` is gone
# — an item whose booking status is merely uncertain carries
# `needs_verification: true` on its note instead.
AtlasBookingReadiness = case_insensitive(Literal["suggested", "needs_advance_booking"])
# TWM-226: which trip endpoint a candidate gateway hub serves. `origin` for a
# hub the traveler passes through to *leave* a hubless origin town, `destination`
# for a hub they pass through to *reach* a hubless destination town.
AtlasHubSide = case_insensitive(Literal["origin", "destination"])
AtlasAccessGap = case_insensitive(Literal["air", "rail"])
# Whole-number estimates and distances: a fractional or "1,500" slip is
# rounded / parsed rather than failing the itinerary.
Estimate = Annotated[int, Field(ge=0), LenientInt]
OptionalEstimate = Annotated[Optional[int], Field(ge=0), LenientInt]
Distance = Annotated[int, Field(gt=0), LenientInt]

# Longest trip the planner will build a day-by-day plan for.
MAX_TRIP_DAYS = 60


class AtlasTransportHub(AgentContent):
    """TWM-226: a plain geographic fact about a candidate gateway city for a
    `TRAVEL` leg whose own endpoint town has no realistic long-haul transport.
    Atlas supplies an unranked plausible set (2-3); deterministic downstream
    feasibility/booking resolves modes, fares, and the final pick. Atlas never
    names a transit mode here -- `long_haul_distance_km` lets a rail-only hub
    with no airport still be assessed downstream."""

    city: Text
    side: AtlasHubSide
    access_gap: AtlasAccessGap
    # All three are positive: a gateway sits a real surface transfer from the
    # town and a real long-haul distance from the trip's other endpoint, so a
    # zero on any of them is a degenerate "hub" that is not a hub.
    last_mile_km: Distance
    last_mile_duration_minutes: Distance
    long_haul_distance_km: Distance


class AtlasAssumption(AgentContent):
    category: AtlasAssumptionCategory
    detail: Text


class AtlasReference(AgentContent):
    status: VerificationStatus
    source_title: OptionalText = None
    source_url: OptionalText = None

    @model_validator(mode="after")
    def unsourced_claim_is_general_guidance(self, info: ValidationInfo) -> "AtlasReference":
        # "Verified" is a claim the output has to back with a source. Without
        # one the honest reading is general guidance -- lowering the claim,
        # never inventing a source -- rather than discarding the itinerary.
        if self.status == "VERIFIED" and not (self.source_title and self.source_url):
            self.status = "GENERAL_GUIDANCE"
            record_heal(info, "reference.unsourced_verified_downgraded")
        return self


class AtlasWorkingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1)
    date: Optional[Text] = None
    places: list[Text] = Field(default_factory=list)


class AtlasWorkingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destinations: list[Text]
    trip_duration: int = Field(ge=1, le=MAX_TRIP_DAYS)
    approved_places: list[Text] = Field(default_factory=list)
    days: list[AtlasWorkingDay]

    @model_validator(mode="after")
    def validate_approved_plan(self) -> "AtlasWorkingPlan":
        if len(self.days) != self.trip_duration:
            raise ValueError("the day plan must have exactly one entry for each day of the trip")
        if [day.day_number for day in self.days] != list(
            range(1, self.trip_duration + 1)
        ):
            raise ValueError("the days of the plan must be numbered 1, 2, 3 and so on")
        allocated = [place for day in self.days for place in day.places]
        normalized = [place.casefold() for place in allocated]
        if len(normalized) != len(set(normalized)):
            raise ValueError("each place must be planned on exactly one day")
        if set(normalized) != {place.casefold() for place in self.approved_places}:
            raise ValueError("the day plan must schedule every approved place and no others")
        return self


class AtlasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_context: TripContext = Field(default_factory=TripContext)
    working_plan: AtlasWorkingPlan

    @model_validator(mode="after")
    def validate_request(self) -> "AtlasRequest":
        validate_phase_state({"trip_context": self.trip_context.model_dump(mode="json")})
        return self


class AtlasTripSummary(AgentContent):
    title: Text
    destinations: list[Text]
    # The number of days in the itinerary (derived by AtlasFinalItinerary);
    # hidden from the model rather than asked to agree with its own day list.
    trip_duration: SkipJsonSchema[int] = Field(default=1, ge=1)
    num_travelers: Annotated[Optional[int], Field(ge=1), IntOrNone] = None
    overview: Text
    route_rationale: Text


class AtlasTimelineItem(AgentContent):
    start_time: OptionalText = None
    end_time: OptionalText = None
    kind: TimelineKind
    title: Text
    location: Text
    detail: Text
    movement_guidance: OptionalText = None
    from_city: OptionalText = None
    to_city: OptionalText = None
    estimated_cost_low: OptionalEstimate = None
    estimated_cost_high: OptionalEstimate = None
    reference: AtlasReference
    # Derived from booking_readiness (present means advance action applies);
    # hidden from the model so the two can never disagree.
    requires_advance_booking: SkipJsonSchema[bool] = False
    booking_readiness: Optional[AtlasBookingReadiness] = None
    # TWM-226: candidate gateway hubs for a TRAVEL leg whose own endpoint town
    # has no realistic long-haul transport -- presented as equal options (the
    # more commonly-used gateway first where there is one). Absent for a
    # normally-connected leg and when Atlas cannot confidently name a hub.
    hubs: Annotated[Optional[list[AtlasTransportHub]], EmptyAsNone] = None

    @model_validator(mode="after")
    def validate_cost_range(self, info: ValidationInfo) -> "AtlasTimelineItem":
        self.estimated_cost_low, self.estimated_cost_high = ordered_range(
            self.estimated_cost_low, self.estimated_cost_high, info, "estimated_cost"
        )
        return self

    @model_validator(mode="before")
    @classmethod
    def _heal_booking_and_cost(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        data = {**data}
        # A model that says "needs advance booking" without picking a readiness
        # has said exactly `needs_advance_booking`.
        flagged = str(data.get("requires_advance_booking")).strip().casefold() in {"true", "yes", "1"}
        if flagged and data.get("booking_readiness") is None:
            data["booking_readiness"] = "needs_advance_booking"
            record_heal(info, "booking_readiness.taken_from_requires_advance_booking")
        # A cost range needs both ends; a lone bound is not a range (the UI only
        # renders one when both exist), so it is dropped rather than half-shown.
        low, high = data.get("estimated_cost_low"), data.get("estimated_cost_high")
        if (low is None) != (high is None):
            data["estimated_cost_low"] = data["estimated_cost_high"] = None
            record_heal(info, "estimated_cost.lone_bound_dropped")
        return data

    @model_validator(mode="after")
    def derive_requires_advance_booking(self) -> "AtlasTimelineItem":
        self.requires_advance_booking = self.booking_readiness is not None
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
        return self


StayTier = Literal["budget", "mid_range", "premium"]
_STAY_TIER_ORDER: tuple[StayTier, ...] = ("budget", "mid_range", "premium")


class AtlasStayTierEstimate(AgentContent):
    """A single tier of TWM-204's non-binding stay price-band estimate --
    never a live/booked price (that stays structurally forbidden on
    TrustedAction), the same estimate-then-redirect honesty framing already
    applied to transit `estimated_cost_low/high`."""

    tier: StayTier
    estimated_cost_low: Estimate
    estimated_cost_high: Estimate

    @model_validator(mode="after")
    def validate_range(self, info: ValidationInfo) -> "AtlasStayTierEstimate":
        self.estimated_cost_low, self.estimated_cost_high = ordered_range(
            self.estimated_cost_low, self.estimated_cost_high, info, "stay_estimate"
        )
        return self


class AtlasDayNote(AgentContent):
    category: Text
    title: Text
    detail: Text
    reference: AtlasReference
    # TWM-217: "worth checking closer to travel; no live source confirmed
    # it." A day-specific verification gap lives here (not a separate list).
    needs_verification: bool = False


class AtlasDay(AgentContent):
    day_number: int = Field(ge=1)
    title: Text
    primary_location: Text
    summary: Text
    timeline: list[AtlasTimelineItem] = Field(min_length=1)
    notes: Annotated[list[AtlasDayNote], NullAsEmptyList] = Field(default_factory=list)
    backup_plan: OptionalText = None
    # TWM-204: present only when this day involves an overnight stay --
    # Atlas's own judgment call, the same as backup_plan above. Absent for
    # a day-trip/pure-transit/departure day with no overnight stay.
    stay_price_estimate: Annotated[Optional[list[AtlasStayTierEstimate]], EmptyAsNone] = None

    @field_validator("stay_price_estimate", mode="before")
    @classmethod
    def _tiers_in_price_order(cls, value: Any, info: ValidationInfo) -> Any:
        if not (isinstance(value, list) and value and all(isinstance(item, dict) for item in value)):
            return value
        order = {tier: index for index, tier in enumerate(_STAY_TIER_ORDER)}
        rank = lambda item: order.get(str(item.get("tier")).strip().casefold().replace("-", "_").replace(" ", "_"))  # noqa: E731
        if any(rank(item) is None for item in value):
            return value
        ordered = sorted(value, key=rank)
        if ordered != value:
            record_heal(info, "stay_price_estimate.tiers_ordered")
        return ordered

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


class AtlasBudgetLine(AgentContent):
    category: Text
    amount_low: Estimate
    amount_high: Estimate
    note: Text

    @model_validator(mode="after")
    def validate_range(self, info: ValidationInfo) -> "AtlasBudgetLine":
        self.amount_low, self.amount_high = ordered_range(
            self.amount_low, self.amount_high, info, "budget_amount"
        )
        return self


class AtlasBudgetSummary(AgentContent):
    currency: Text
    lines: list[AtlasBudgetLine] = Field(min_length=1)
    # Always the sum of the lines (calculated below); hidden from the model.
    total_low: SkipJsonSchema[int] = Field(default=0, ge=0)
    total_high: SkipJsonSchema[int] = Field(default=0, ge=0)

    @model_validator(mode="after")
    def calculate_totals(self) -> "AtlasBudgetSummary":
        self.total_low = sum(line.amount_low for line in self.lines)
        self.total_high = sum(line.amount_high for line in self.lines)
        return self


class AtlasPracticalNote(AgentContent):
    category: Text
    title: Text
    detail: Text
    reference: AtlasReference
    # TWM-217: a trip-wide verification gap lives here (not a separate list).
    needs_verification: bool = False


class AtlasSource(AgentContent):
    title: Text
    url: Text
    supports: list[Text] = Field(min_length=1)


class AtlasFinalItinerary(AgentContent):
    trip_summary: AtlasTripSummary
    days: list[AtlasDay] = Field(min_length=1)
    budget_summary: AtlasBudgetSummary
    practical_notes: Annotated[list[AtlasPracticalNote], NullAsEmptyList] = Field(default_factory=list)
    sources: Annotated[list[AtlasSource], NullAsEmptyList] = Field(default_factory=list)
    assumptions: Annotated[list[AtlasAssumption], NullAsEmptyList] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _heal_shape(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        data = {**data}
        # `assumptions` sits on the itinerary, never inside trip_summary; lists
        # merge without losing anything from either side.
        summary = data.get("trip_summary")
        stray = summary.get("assumptions") if isinstance(summary, dict) else None
        existing = data.get("assumptions") or []
        if isinstance(stray, list) and stray and isinstance(existing, list):
            data["assumptions"] = existing + stray
            data["trip_summary"] = {k: v for k, v in summary.items() if k != "assumptions"}
            record_heal(info, "assumptions.relocated_into_final_itinerary")
        # A day's number is its position in the list.
        data["days"], changed = number_by_position(data.get("days"), "day_number")
        if changed:
            record_heal(info, "days.numbered_by_position")
        return data

    @model_validator(mode="after")
    def derive_trip_duration(self) -> "AtlasFinalItinerary":
        self.trip_summary.trip_duration = len(self.days)
        return self


class AtlasAgentOutput(AgentContent):
    final_itinerary: AtlasFinalItinerary


class AtlasResponse(AtlasAgentOutput):
    agent_meta: AgentMeta
