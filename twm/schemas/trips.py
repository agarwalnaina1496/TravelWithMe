"""Canonical HTTP contracts for database-backed trips."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AgentMeta
from .scout import BoundedMessage


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
    "advice_entry",
    "discover_entry",
    "known_destination_entry",
]

_MESSAGE_COMMANDS = {"traveler_message", "advice_entry"}


class TripCommandRequest(BaseModel):
    """A browser intent; canonical TripState is deliberately not accepted."""

    model_config = ConfigDict(extra="forbid")

    command: TripCommandName
    expected_version: int = Field(ge=1)
    idempotency_key: UUID
    message: BoundedMessage | None = None
    option_id: str | None = Field(default=None, min_length=1, max_length=200)
    destination: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_command_fields(self) -> "TripCommandRequest":
        if self.command == "traveler_message" and not (
            self.message and self.message.strip()
        ):
            raise ValueError("traveler_message requires message")
        if self.command == "advice_entry" and not (
            self.message and self.message.strip()
        ):
            raise ValueError("advice_entry requires message")
        if self.command == "select_destination" and not self.option_id:
            raise ValueError("select_destination requires option_id")
        if self.command not in _MESSAGE_COMMANDS and self.message is not None:
            raise ValueError("message is allowed only for traveler_message or advice_entry")
        if self.command != "select_destination" and self.option_id is not None:
            raise ValueError("option_id is allowed only for select_destination")
        if self.command != "known_destination_entry" and self.destination is not None:
            raise ValueError("destination is allowed only for known_destination_entry")
        return self


class TripCommandResponse(BaseModel):
    trip: TripResponse
    message: str | None = None
    agent_meta: AgentMeta | None = None
