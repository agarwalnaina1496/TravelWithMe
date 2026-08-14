"""Persistence boundary shared by the service and repository."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class GuestSession:
    id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class TripRecord:
    id: UUID
    guest_session_id: UUID
    title: str
    product_mode: str
    trip_state: dict[str, Any]
    ui_state: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TripCommandRecord:
    request_hash: str
    response: dict[str, Any]


@dataclass(frozen=True)
class RecommendationRecord:
    """A single archived matcher round (TWM-153) — success, soft-fail, or a
    terminal failure outcome; trip_type/traveler_criteria/suggestions are
    None for failure outcomes that never had ranked options."""

    trip_id: UUID
    version: int
    status: str
    message: str
    trip_type: str | None
    options: list[dict[str, Any]]
    traveler_criteria: list[dict[str, Any]] | None
    constraint_adjustment_suggestions: list[str] | None
    agent_meta: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class ItineraryVersionRecord:
    """A single archived itinerary version (TWM-155) — the outgoing
    current_version at the moment a proposed revision is accepted."""

    trip_id: UUID
    version: int
    source_guide_revision: int
    result: dict[str, Any]
    created_at: datetime


class VersionConflictError(Exception):
    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__(f"Expected version is stale; current version is {current_version}.")


class TripRepository(Protocol):
    async def resolve_guest(self, token_hash: str, lifetime_days: int) -> GuestSession | None: ...
    async def create_guest(self, token_hash: str, lifetime_days: int) -> GuestSession: ...
    async def list_trips(self, guest_id: UUID) -> list[TripRecord]: ...
    async def create_trip(self, guest_id: UUID, title: str, product_mode: str, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord: ...
    async def get_trip(self, guest_id: UUID, trip_id: UUID) -> TripRecord | None: ...
    async def replace_trip(self, guest_id: UUID, trip_id: UUID, expected_version: int, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord | None: ...
    async def rename_trip(self, guest_id: UUID, trip_id: UUID, expected_version: int, title: str) -> TripRecord | None: ...
    async def update_ui_state(self, guest_id: UUID, trip_id: UUID, expected_version: int, ui_state: dict[str, Any]) -> TripRecord | None: ...
    async def get_command(self, guest_id: UUID, trip_id: UUID, idempotency_key: UUID) -> TripCommandRecord | None: ...
    async def get_latest_recommendation(self, guest_id: UUID, trip_id: UUID) -> RecommendationRecord | None: ...
    async def list_itinerary_versions(self, guest_id: UUID, trip_id: UUID) -> list[ItineraryVersionRecord]: ...
    async def commit_command(self, guest_id: UUID, trip_id: UUID, expected_version: int, idempotency_key: UUID, request_hash: str, trip_state: dict[str, Any], response_trip_state: dict[str, Any], response: dict[str, Any], touched_branches: frozenset[str], new_recommendation: dict[str, Any] | None = None, new_itinerary_version: dict[str, Any] | None = None) -> TripRecord | TripCommandRecord | None: ...
