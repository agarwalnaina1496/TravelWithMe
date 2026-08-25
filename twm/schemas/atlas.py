"""Atlas input and rich final-itinerary contracts."""

import re
from datetime import date
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..trust_boundary import validate_phase_state
from .common import AgentMeta
from .trip_context import TripContext


AtlasText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
VerificationStatus = Literal["VERIFIED", "GENERAL_GUIDANCE"]
TimelineKind = Literal["TRAVEL", "STAY", "MEAL", "ACTIVITY", "FREE_TIME"]
AtlasAssumptionCategory = Literal[
    "dates",
    "arrival_departure_window",
    "stay_area",
    "budget",
    "traveler_count",
    "other",
]
# Atlas never sees a real reservation, so "confirmed" is deliberately absent:
# confirmed logistics are an application-owned anchor added by a later capability.
AtlasBookingReadiness = Literal["suggested", "needs_advance_booking", "unresolved"]

_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class AtlasAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: AtlasAssumptionCategory
    detail: AtlasText


LogisticsAnchorType = Literal["transport", "stay", "activity"]


class AtlasConfirmedAnchor(BaseModel):
    """An application-owned confirmed logistics fact Atlas must respect."""

    model_config = ConfigDict(extra="forbid")

    type: LogisticsAnchorType
    label: AtlasText
    detail: AtlasText
    day_number: Optional[int] = Field(default=None, ge=1)


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
    confirmed_anchors: list[AtlasConfirmedAnchor] = Field(default_factory=list)

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
    date_range: Optional[AtlasText] = None
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
    from_place: Optional[AtlasText] = None
    to_place: Optional[AtlasText] = None
    display_label: Optional[AtlasText] = None
    departure_date: Optional[date] = None
    departure_month: Optional[AtlasText] = None
    estimated_cost_low: Optional[int] = Field(default=None, ge=0)
    estimated_cost_high: Optional[int] = Field(default=None, ge=0)
    reference: AtlasReference
    requires_advance_booking: bool = False
    booking_readiness: Optional[AtlasBookingReadiness] = None

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
            if self.from_place is not None or self.to_place is not None:
                raise ValueError(
                    "from_place/to_place are allowed only when kind is TRAVEL"
                )
        if (self.from_city is None) != (self.to_city is None):
            raise ValueError(
                "from_city and to_city must both be present or both be absent"
            )
        return self

    @model_validator(mode="after")
    def validate_departure_precision(self) -> "AtlasTimelineItem":
        if self.kind != "TRAVEL":
            if self.departure_date is not None or self.departure_month is not None:
                raise ValueError(
                    "departure_date/departure_month are allowed only when kind is TRAVEL"
                )
            return self
        if self.departure_date is not None and self.departure_month is not None:
            raise ValueError(
                "departure_date and departure_month are mutually exclusive"
            )
        if self.departure_month is not None and not _MONTH_PATTERN.match(
            self.departure_month
        ):
            raise ValueError("departure_month must be a validated YYYY-MM value")
        return self


class AtlasDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_number: int = Field(ge=1)
    date: Optional[AtlasText] = None
    title: AtlasText
    primary_location: AtlasText
    summary: AtlasText
    timeline: list[AtlasTimelineItem] = Field(min_length=1)
    seasonal_guidance: AtlasText
    permit_or_ticket_guidance: AtlasText
    backup_plan: Optional[AtlasText] = None


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
    budget_fit: AtlasText

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


class AtlasSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: AtlasText
    url: AtlasText
    supports: list[AtlasText] = Field(min_length=1)


class AtlasUnresolvedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: AtlasText
    generic_guidance: AtlasText


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
    unresolved: list[AtlasUnresolvedItem]


class AtlasResponse(AtlasAgentOutput):
    agent_meta: AgentMeta


def _validate_optional_range(low: Optional[int], high: Optional[int]) -> None:
    if (low is None) != (high is None):
        raise ValueError("cost range requires both low and high or neither")
    if low is not None and high is not None and high < low:
        raise ValueError("cost high must be at least cost low")
