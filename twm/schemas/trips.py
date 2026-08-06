"""Canonical HTTP contracts for database-backed trips."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TripCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    product_mode: Literal["self_led", "twm_led"] = "self_led"
    trip_state: dict[str, Any] = Field(default_factory=dict)
    ui_state: dict[str, Any] = Field(default_factory=dict)


class TripReplaceRequest(BaseModel):
    expected_version: int = Field(ge=1)
    trip_state: dict[str, Any]
    ui_state: dict[str, Any]


class TripRenameRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=120)


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
