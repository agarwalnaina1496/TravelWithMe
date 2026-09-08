"""``TripView`` — the one server-composed read model ``GET /trips/{id}``
returns (TWM-217).

Every field below is produced by a ``TripViewService._compose_*`` method —
nothing is wired straight from a router or a repository read. The
fitness-function test (``tests/unit/trip_view/test_composer_owns_every_field``)
enforces that rule against this module's field list.
"""

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .scout import TripStage

_forbid = ConfigDict(extra="forbid")

TravelerSource = Literal["party", "itinerary_estimate", "conversational", "unknown"]
DateSource = Literal["conversational", "none"]
DatePrecision = Literal["exact", "month", "none"]


class TripViewLifecycle(BaseModel):
    model_config = _forbid
    stage: TripStage
    status: str
    active_agent: Optional[str]
    selected_option: Optional[dict[str, Any]]


class ContextRecapItem(BaseModel):
    model_config = _forbid
    key: str
    label: str
    value: str


class TripViewPlanDay(BaseModel):
    model_config = _forbid
    day_number: int
    places: list[str]
    pace: Optional[str] = None
    buffer_note: Optional[str] = None


class TripViewPlan(BaseModel):
    model_config = _forbid
    places: list[str]
    day_plan: list[TripViewPlanDay]
    frozen: bool
    awaiting: Optional[str]


class TripViewMatcher(BaseModel):
    model_config = _forbid
    last_message: Optional[str]
    awaiting: Optional[str]
    has_recommendation: bool


class TravelerParty(BaseModel):
    model_config = _forbid
    adults: int
    children: int
    infants: int


class SummaryTravelers(BaseModel):
    model_config = _forbid
    value: Optional[str]
    exact: Optional[TravelerParty]
    source: TravelerSource


class SummaryDates(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    precision: DatePrecision
    departure: Optional[str] = None
    return_: Optional[str] = Field(default=None, serialization_alias="return")
    month: Optional[str] = None
    label: Optional[str]
    source: DateSource


class SummaryBudget(BaseModel):
    model_config = _forbid
    low: int
    high: int
    currency: str


class TripViewSummary(BaseModel):
    model_config = _forbid
    title: str
    destinations: list[str]
    duration_days: int
    overview: str
    route_rationale: str
    travelers: SummaryTravelers
    dates: SummaryDates
    budget: SummaryBudget


class TripViewBooking(BaseModel):
    model_config = _forbid
    party: Optional[TravelerParty]


class BudgetLine(BaseModel):
    model_config = _forbid
    category: str
    low: int
    high: int
    note: str


class BudgetBreakdown(BaseModel):
    model_config = _forbid
    fit_note: str
    lines: list[BudgetLine]
    estimated_for_travelers: Optional[int]
    party_changed_since: bool


class OpenGap(BaseModel):
    model_config = _forbid
    what: str
    resolution: Literal["set_party"]
    detail: str


class BeforeYouGoItem(BaseModel):
    model_config = _forbid
    title: str
    detail: str
    verify: bool


class TripView(BaseModel):
    model_config = _forbid

    id: UUID
    title: str
    product_mode: Literal["self_led", "twm_led"]
    version: int
    # Structural / passthrough fields alongside `id` / `title` / `version` —
    # the per-viewer presentation bag (TWM-171), stored and returned verbatim.
    ui_state: dict[str, Any]
    lifecycle: TripViewLifecycle
    context_recap: list[ContextRecapItem]
    plan: Optional[TripViewPlan]
    matcher: TripViewMatcher

    # null until an itinerary exists
    summary: Optional[TripViewSummary]
    booking: Optional[TripViewBooking]
    budget_breakdown: Optional[BudgetBreakdown]
    open_gaps: Optional[list[OpenGap]]
    before_you_go: Optional[list[BeforeYouGoItem]]


class TravelWindow(BaseModel):
    """A list-item date hint composed from ``trip_context.travel_dates`` — the
    structured half of what ``TripViewSummary.dates`` carries, enough for
    ``DashboardHome`` to rank a hero trip by when it happens without the
    client parsing the loose conversational string. ``None`` when nothing
    confidently interpretable was said."""

    model_config = _forbid
    precision: Literal["exact", "month"]
    departure: Optional[str] = None
    month: Optional[str] = None


class TripListItem(BaseModel):
    """``GET /trips`` — a thin ``TripView`` subset (no ``TripSummaryState``)."""

    model_config = _forbid

    id: UUID
    title: str
    product_mode: Literal["self_led", "twm_led"]
    version: int
    created_at: datetime
    updated_at: datetime
    lifecycle: TripViewLifecycle
    context_recap: list[ContextRecapItem]
    travel_window: Optional[TravelWindow]
    has_places: bool
    has_day_plan: bool
    has_itinerary: bool
    awaiting: Optional[str]
    has_recommendation: bool


class TripListResponse(BaseModel):
    trips: list[TripListItem]
