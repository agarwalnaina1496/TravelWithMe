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
    async def get_command(self, guest_id: UUID, trip_id: UUID, idempotency_key: UUID) -> TripCommandRecord | None: ...
    async def commit_command(self, guest_id: UUID, trip_id: UUID, expected_version: int, idempotency_key: UUID, request_hash: str, trip_state: dict[str, Any], response: dict[str, Any]) -> TripRecord | TripCommandRecord | None: ...
