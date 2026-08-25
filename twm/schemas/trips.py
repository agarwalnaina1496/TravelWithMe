"""Canonical HTTP contracts for database-backed trips."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta
from .flight_search import DepartureMonth
from .logistics import LogisticsConfirmationInput
from .recommendations import NonEmptyString, RecommendationOption, TravelerCriterion
from .scout import BoundedMessage, TripStage
from .trip_context import DESTINATIONS_KEY, FIXED_KEYS


class MeridianRefinementReference(BaseModel):
    """Canonical identity of the recommendation option a refinement builds on."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["single", "circuit"]
    id: NonEmptyString


class MeridianRefinement(BaseModel):
    """Additive deterministic More like this refinement input for Meridian."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["MORE_LIKE_THIS"]
    reference: MeridianRefinementReference
    instructions: BoundedMessage | None = None


class TripBookingDateInput(BaseModel):
    """update_booking_dates command payload (TWM-201): post-freeze booking-
    date precision for the traveler's own flight legs. Exact XOR month,
    same shape/constraint as FlightSearchRequest's departure_date/
    departure_month so booking legs can pass this straight through to a
    flight search once persisted. Never accepts free text — the UI must
    not guess a year from a month label; only a validated exact date or a
    validated YYYY-MM window is accepted here."""

    model_config = ConfigDict(extra="forbid")

    departure_date: date | None = None
    departure_month: DepartureMonth | None = None

    @model_validator(mode="after")
    def validate_precision(self) -> "TripBookingDateInput":
        if self.departure_date is not None and self.departure_month is not None:
            raise ValueError("departure_date and departure_month are mutually exclusive")
        if self.departure_date is None and self.departure_month is None:
            raise ValueError("one of departure_date or departure_month is required")
        return self


class TripCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    product_mode: Literal["self_led", "twm_led"] = "self_led"
    trip_context: dict[str, Any] = Field(min_length=1)


# Which specialist should own a trip from its very first message onward —
# the traveler's own up-front choice (Discover vs. Plan a Trip), not
# something Scout classifies. Meaningful only on a trip's first turn;
# TripCommandRequest.entry_intent carries the same signal for that one
# case, and is ignored on every later turn once an agent already owns
# the trip.
EntryIntent = Literal["discover", "known_destination"]


class TripFirstMessageRequest(BaseModel):
    """First-turn orchestration input (TWM-189) — no trip exists yet, so
    there is no expected_version/idempotency replay the way TripCommandRequest
    has for an established trip. Always a traveler_message-shaped turn;
    entry_intent decides which specialist receives it."""

    model_config = ConfigDict(extra="forbid")

    entry_intent: EntryIntent
    title: str = Field(default="Untitled Trip", min_length=1, max_length=120)
    product_mode: Literal["self_led", "twm_led"] = "self_led"
    message: BoundedMessage


class TripRenameRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)


class TripUiStateRequest(BaseModel):
    """Versioned presentation state; canonical TripState is not accepted."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    ui_state: dict[str, Any]


class TripResponse(BaseModel):
    id: UUID
    title: str
    product_mode: Literal["self_led", "twm_led"]
    trip_state: dict[str, Any]
    ui_state: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class TripSummaryItineraryState(BaseModel):
    status: str | None = None


# trip_context is otherwise free-form (Scout extracts whatever semantic
# key fits the conversation for anything outside FIXED_KEYS/destinations),
# so this is deliberately just the addressable, canonically-named subset —
# the same recap TWM-UI's tripLifecycle.js/dashboardTracks.js/discoverChat.js
# render, all three keyed off this exact same field list (TWM-159, TWM-182).
# selected_option is intentionally excluded: it's the Discover-path-only
# "which exact recommendation option" identity used for re-selection
# matching in Destinations.jsx, never a "destination is known" display
# signal — that job belongs to `destinations` alone, for both entry paths.
SUMMARY_TRIP_CONTEXT_FIELDS = (*FIXED_KEYS, DESTINATIONS_KEY)


class TripSummaryState(BaseModel):
    stage: TripStage = "new"
    itinerary_state: TripSummaryItineraryState = Field(default_factory=TripSummaryItineraryState)
    trip_context: dict[str, Any] = Field(default_factory=dict)
    # TWM-182: a cheap derived planning-progress signal — never the full
    # planner_state (day_plan/frozen_plan/superseded_planner_states are
    # unbounded and stay off the list card by design). Lets My Trips/Landing
    # tell "mid-conversation" from "draft ready" without a second fetch.
    awaiting: str | None = None
    has_day_plan: bool = False
    has_places: bool = False
    # TWM-190: mirrors has_day_plan's role for the Discover side — whether a
    # matcher round has ever been archived for this trip, regardless of
    # current stage. Lets a "matching"-stage resume distinguish a genuinely
    # fresh Meridian conversation from a refinement round awaiting
    # clarification (stage stays "matching" but a prior recommendation
    # already exists), without a second fetch.
    has_recommendation: bool = False


class TripSummary(BaseModel):
    """My Trips / Landing list item (TWM-159, extended TWM-182) — a small
    recap, not the full trip_state. The Atlas itinerary result and
    matcher/logistics state never belong on a card the list screen never
    reads them from; planner_state contributes only the three cheap derived
    fields on TripSummaryState above, never its own nested day_plan/
    frozen_plan/history."""

    id: UUID
    title: str
    product_mode: Literal["self_led", "twm_led"]
    trip_state: TripSummaryState
    version: int
    created_at: datetime
    updated_at: datetime


class TripListResponse(BaseModel):
    trips: list[TripSummary]


class TripRecommendationsResponse(BaseModel):
    """The latest archived matcher round (TWM-153) — success/soft-fail
    (ranked options) or a terminal failure outcome (empty options)."""

    version: int
    status: str
    message: str
    trip_type: Literal["single", "circuit", "mixed"] | None = None
    options: list[RecommendationOption] = Field(default_factory=list)
    traveler_criteria: list[TravelerCriterion] | None = None
    constraint_adjustment_suggestions: list[NonEmptyString] | None = None
    agent_meta: AgentMeta
    created_at: datetime


class ItineraryVersionDaySummary(BaseModel):
    day_number: int
    title: NonEmptyString


class TripItineraryVersionSummary(BaseModel):
    """A lightweight summary of one archived itinerary version (TWM-155) —
    day titles only, not the full nested AtlasFinalItinerary result."""

    version: int
    source_guide_revision: int
    created_at: datetime
    days: list[ItineraryVersionDaySummary]


class TripItineraryVersionsResponse(BaseModel):
    versions: list[TripItineraryVersionSummary]


class TripItineraryResponse(BaseModel):
    """The active itinerary's full Atlas result (TWM-159) — moved out of
    GET /trips/{id} into its own endpoint since only the Trip Dashboard
    screen ever reads it."""

    version: int
    source_guide_revision: int
    result: dict[str, Any]
    created_at: datetime


class TripConflictResponse(BaseModel):
    detail: str = "Trip has a newer version."
    current_version: int


TripCommandName = Literal[
    "traveler_message",
    "continue",
    "select_destination",
    "start_planning",
    "approve_plan",
    "more_like_this",
    "reopen_destination_revisit",
    "reopen_destination_fresh",
    "start_itinerary",
    "confirm_logistics",
    "accept_itinerary_revision",
    "keep_current_itinerary",
    "update_booking_dates",
]

_MESSAGE_COMMANDS = {"traveler_message"}


class TripCommandRequest(BaseModel):
    """A browser intent; canonical TripState is deliberately not accepted."""

    model_config = ConfigDict(extra="forbid")

    command: TripCommandName
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    message: BoundedMessage | None = None
    # Meaningful only on a trip's first turn (no agent owns it yet) — the
    # traveler's own Discover-vs-Plan-a-Trip choice, not something Scout
    # classifies. Ignored on any later turn once an agent already owns the
    # trip (TripCommandName no longer has separate discover_entry/
    # known_destination_entry commands for this — it's carried as data on
    # the one traveler_message command instead, same shape whether it's
    # this trip's first turn or its fiftieth).
    entry_intent: EntryIntent | None = None
    option_id: str | None = Field(default=None, min_length=1, max_length=200)
    refinement: MeridianRefinement | None = None
    logistics_confirmation: LogisticsConfirmationInput | None = None
    booking_date_update: TripBookingDateInput | None = None

    @model_validator(mode="after")
    def validate_command_fields(self) -> "TripCommandRequest":
        if self.command == "traveler_message" and not (
            self.message and self.message.strip()
        ):
            raise ValueError("traveler_message requires message")
        if self.command == "select_destination" and not self.option_id:
            raise ValueError("select_destination requires option_id")
        if self.command == "more_like_this" and self.refinement is None:
            raise ValueError("more_like_this requires refinement")
        if self.command == "confirm_logistics" and self.logistics_confirmation is None:
            raise ValueError("confirm_logistics requires logistics_confirmation")
        if self.command != "confirm_logistics" and self.logistics_confirmation is not None:
            raise ValueError(
                "logistics_confirmation is allowed only for confirm_logistics"
            )
        if self.command == "update_booking_dates" and self.booking_date_update is None:
            raise ValueError("update_booking_dates requires booking_date_update")
        if self.command != "update_booking_dates" and self.booking_date_update is not None:
            raise ValueError(
                "booking_date_update is allowed only for update_booking_dates"
            )
        if self.command not in _MESSAGE_COMMANDS and self.message is not None:
            raise ValueError("message is allowed only for traveler_message")
        if self.command != "traveler_message" and self.entry_intent is not None:
            raise ValueError("entry_intent is allowed only for traveler_message")
        if self.command != "select_destination" and self.option_id is not None:
            raise ValueError("option_id is allowed only for select_destination")
        if self.command != "more_like_this" and self.refinement is not None:
            raise ValueError("refinement is allowed only for more_like_this")
        return self


class TripCommandResponse(BaseModel):
    trip: TripResponse
    message: str | None = None
    agent_meta: AgentMeta | None = None
