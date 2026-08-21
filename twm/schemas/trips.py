"""Canonical HTTP contracts for database-backed trips."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta
from .logistics import LogisticsConfirmationInput
from .recommendations import NonEmptyString, RecommendationOption, TravelerCriterion
from .scout import BoundedMessage, TripStage


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


class TripCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    product_mode: Literal["self_led", "twm_led"] = "self_led"
    trip_context: dict[str, Any] = Field(min_length=1)


# The two commands that can legitimately start a trip with no trip_id yet
# (TWM-189).
FirstMessageCommandName = Literal["discover_entry", "known_destination_entry"]


class TripFirstMessageRequest(BaseModel):
    """First-message orchestration input (TWM-189) — no trip exists yet, so
    there is no expected_version/idempotency replay the way TripCommandRequest
    has for an established trip."""

    model_config = ConfigDict(extra="forbid")

    command: FirstMessageCommandName
    title: str = Field(default="Untitled Trip", min_length=1, max_length=120)
    product_mode: Literal["self_led", "twm_led"] = "self_led"
    message: BoundedMessage | None = None
    destination: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_command_fields(self) -> "TripFirstMessageRequest":
        if self.command == "known_destination_entry" and not (
            self.destination and self.destination.strip()
        ):
            raise ValueError("known_destination_entry requires destination")
        if self.command != "known_destination_entry" and self.destination is not None:
            raise ValueError("destination is allowed only for known_destination_entry")
        return self


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


# trip_context is free-form (Scout extracts whatever field names fit the
# conversation); this is the same recap subset My Trips/Landing actually
# render (TWM-159) — kept in sync with TWM-UI's tripLifecycle.js
# RECAP_FIELDS plus selected_option (contextDestination). `destinations`
# (TWM-182) is the known-destination entry path's counterpart to
# selected_option — without it, a known-destination trip's Route track
# can't resolve its destination name when TWM-UI renders the Dashboard
# straight from this summary, before any full single-trip fetch.
SUMMARY_TRIP_CONTEXT_FIELDS = (
    "origin", "budget", "duration_days", "travelers", "travel_window", "month", "dates", "selected_option", "destinations",
)


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
    "discover_entry",
    "known_destination_entry",
    "more_like_this",
    "reopen_destination_revisit",
    "reopen_destination_fresh",
    "start_itinerary",
    "confirm_logistics",
    "accept_itinerary_revision",
    "keep_current_itinerary",
]

_MESSAGE_COMMANDS = {"traveler_message"}
# discover_entry may optionally carry the traveler's first message (e.g. an
# answer to "where are you traveling from?") — unlike the commands above,
# it's never required, since Meridian can still be invoked cold.
_OPTIONAL_MESSAGE_COMMANDS = {"discover_entry"}


class TripCommandRequest(BaseModel):
    """A browser intent; canonical TripState is deliberately not accepted."""

    model_config = ConfigDict(extra="forbid")

    command: TripCommandName
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    message: BoundedMessage | None = None
    option_id: str | None = Field(default=None, min_length=1, max_length=200)
    destination: str | None = Field(default=None, min_length=1, max_length=200)
    refinement: MeridianRefinement | None = None
    logistics_confirmation: LogisticsConfirmationInput | None = None

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
        if self.command not in _MESSAGE_COMMANDS and self.command not in _OPTIONAL_MESSAGE_COMMANDS and self.message is not None:
            raise ValueError("message is allowed only for traveler_message or discover_entry")
        if self.command != "select_destination" and self.option_id is not None:
            raise ValueError("option_id is allowed only for select_destination")
        if self.command != "known_destination_entry" and self.destination is not None:
            raise ValueError("destination is allowed only for known_destination_entry")
        if self.command != "more_like_this" and self.refinement is not None:
            raise ValueError("refinement is allowed only for more_like_this")
        return self


class TripCommandResponse(BaseModel):
    trip: TripResponse
    message: str | None = None
    agent_meta: AgentMeta | None = None
