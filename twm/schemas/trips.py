"""Canonical HTTP contracts for database-backed trips."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta
from .logistics import LogisticsConfirmationInput
from .recommendations import NonEmptyString
from .scout import BoundedMessage


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


class TripSummary(BaseModel):
    id: UUID
    title: str
    product_mode: Literal["self_led", "twm_led"]
    trip_state: dict[str, Any]
    ui_state: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


class TripListResponse(BaseModel):
    trips: list[TripSummary]


class TripConflictResponse(BaseModel):
    detail: str = "Trip has a newer version."
    current_version: int


TripCommandName = Literal[
    "traveler_message",
    "continue",
    "select_destination",
    "start_planning",
    "approve_places",
    "approve_plan",
    "new_journey",
    "scout_entry",
    "discover_entry",
    "known_destination_entry",
    "more_like_this",
    "start_itinerary",
    "confirm_logistics",
    "accept_itinerary_revision",
    "keep_current_itinerary",
]

_MESSAGE_COMMANDS = {"traveler_message", "scout_entry"}
# discover_entry/known_destination_entry may optionally carry a free-text
# message too (Plan-a-trip's "anything else" field) — unlike the commands
# above, it's never required, and Scout's own intent-based handoff is
# discarded when it's used this way (see apply_scout_extraction_only).
_OPTIONAL_MESSAGE_COMMANDS = {"discover_entry", "known_destination_entry"}

# UI-owned deterministic intake (TWM-140-plan-trip): the "Plan a trip" flow
# collects these fixed, common fields itself before handing off to Meridian
# or Guide, so the agent never has to ask for them again. This is a narrow,
# explicit ownership carve-out from trip_context's normal Scout/Meridian
# extraction — bounded to exactly these keys so the UI cannot inject
# arbitrary trip_context via this path.
ALLOWED_ENTRY_CONTEXT_KEYS = {"origin", "budget", "travelers", "duration", "travel_window"}


class TripCommandRequest(BaseModel):
    """A browser intent; canonical TripState is deliberately not accepted."""

    model_config = ConfigDict(extra="forbid")

    command: TripCommandName
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    message: BoundedMessage | None = None
    option_id: str | None = Field(default=None, min_length=1, max_length=200)
    destination: str | None = Field(default=None, min_length=1, max_length=200)
    trip_context: dict[str, NonEmptyString] | None = None
    refinement: MeridianRefinement | None = None
    logistics_confirmation: LogisticsConfirmationInput | None = None

    @model_validator(mode="after")
    def validate_command_fields(self) -> "TripCommandRequest":
        if self.command == "traveler_message" and not (
            self.message and self.message.strip()
        ):
            raise ValueError("traveler_message requires message")
        if self.command == "scout_entry" and not (
            self.message and self.message.strip()
        ):
            raise ValueError("scout_entry requires message")
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
            raise ValueError(
                "message is allowed only for traveler_message, scout_entry, discover_entry, or known_destination_entry"
            )
        if self.command != "select_destination" and self.option_id is not None:
            raise ValueError("option_id is allowed only for select_destination")
        if self.command != "known_destination_entry" and self.destination is not None:
            raise ValueError("destination is allowed only for known_destination_entry")
        if self.command != "more_like_this" and self.refinement is not None:
            raise ValueError("refinement is allowed only for more_like_this")
        if self.trip_context is not None:
            if self.command not in {"discover_entry", "known_destination_entry"}:
                raise ValueError(
                    "trip_context is allowed only for discover_entry or known_destination_entry"
                )
            unknown_keys = set(self.trip_context) - ALLOWED_ENTRY_CONTEXT_KEYS
            if unknown_keys:
                raise ValueError(f"trip_context has unsupported keys: {sorted(unknown_keys)}")
        return self


class TripCommandResponse(BaseModel):
    trip: TripResponse
    message: str | None = None
    agent_meta: AgentMeta | None = None
