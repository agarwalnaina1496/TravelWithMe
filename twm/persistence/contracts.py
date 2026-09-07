"""Persistence boundary shared by the service and repository."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

# TWM-191: how a trip's non-branch state is stored on the `twm_app.trips`
# row. `stage` / `status` / `active_agent` are real indexed columns;
# everything else stays in the `trip_state` jsonb blob. `trip_id` is neither
# — it is the row PK, injected into the in-memory dict by the command
# service and never persisted inside `trip_state`. The dedicated branch
# tables (matcher_state / planner_state / itinerary_state / booking_setup)
# are enumerated separately as `TOUCHABLE_BRANCHES` in
# `services/trip_commands/state.py`.
LIFECYCLE_COLUMN_FIELDS = ("stage", "status", "active_agent")
BLOB_STATE_FIELDS = ("trip_context", "advisor_state", "selected_option")


@dataclass(frozen=True)
class GuestSession:
    id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    password_hash: str
    created_at: datetime


@dataclass(frozen=True)
class TripOwner:
    """Resolves which trips a request can reach: an authenticated user's
    trips (user_id), or an unclaimed guest session's trips otherwise. Both
    fields are always populated (every request still carries/creates a
    guest session — TWM-64), but user_id takes precedence once set: a
    claimed trip is superseded from its originating guest session as an
    access path (TWM-179)."""

    guest_session_id: UUID
    user_id: UUID | None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


@dataclass(frozen=True)
class TripRecord:
    id: UUID
    guest_session_id: UUID
    user_id: UUID | None
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
    """The trip's archived Atlas itinerary (TWM-155). Post-TWM-216 the
    revision-review flow is gone, so there is exactly one row per trip —
    written once by `start_itinerary`. `itinerary_state.current_version`
    points at it; the full `result` blob is never inlined into `trip_state`."""

    trip_id: UUID
    version: int
    source_guide_revision: int
    result: dict[str, Any]
    created_at: datetime


class VersionConflictError(Exception):
    def __init__(self, current_version: int):
        self.current_version = current_version
        super().__init__(f"Expected version is stale; current version is {current_version}.")


class DuplicateEmailError(Exception):
    def __init__(self, email: str):
        self.email = email
        super().__init__(f"Email is already registered: {email}")


class TripRepository(Protocol):
    async def resolve_guest(self, token_hash: str, lifetime_days: int) -> GuestSession | None: ...
    async def create_guest(self, token_hash: str, lifetime_days: int) -> GuestSession: ...
    async def create_user(self, email: str, password_hash: str) -> User: ...
    async def get_user_by_email(self, email: str) -> User | None: ...
    async def get_user_by_id(self, user_id: UUID) -> User | None: ...
    async def claim_guest_trips(self, guest_session_id: UUID, user_id: UUID) -> int: ...
    async def list_trips(self, owner: TripOwner, limit: int = 200) -> list[TripRecord]: ...
    async def create_trip(self, guest_id: UUID, user_id: UUID | None, title: str, product_mode: str, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord: ...
    async def get_trip(self, owner: TripOwner, trip_id: UUID) -> TripRecord | None: ...
    async def get_trip_core(self, owner: TripOwner, trip_id: UUID) -> TripRecord | None: ...
    async def replace_trip(self, owner: TripOwner, trip_id: UUID, expected_version: int, trip_state: dict[str, Any], ui_state: dict[str, Any]) -> TripRecord | None: ...
    async def rename_trip(self, owner: TripOwner, trip_id: UUID, expected_version: int, title: str) -> TripRecord | None: ...
    async def update_ui_state(self, owner: TripOwner, trip_id: UUID, expected_version: int, ui_state: dict[str, Any]) -> TripRecord | None: ...
    async def get_command(self, owner: TripOwner, trip_id: UUID, idempotency_key: UUID) -> TripCommandRecord | None: ...
    async def get_latest_recommendation(self, owner: TripOwner, trip_id: UUID) -> RecommendationRecord | None: ...
    async def trip_ids_with_recommendations(self, owner: TripOwner, trip_ids: list[UUID]) -> set[UUID]: ...
    async def get_current_itinerary(self, owner: TripOwner, trip_id: UUID) -> ItineraryVersionRecord | None: ...
    async def commit_command(self, owner: TripOwner, trip_id: UUID, expected_version: int, idempotency_key: UUID, request_hash: str, trip_state: dict[str, Any], response_trip_state: dict[str, Any], response: dict[str, Any], touched_branches: frozenset[str], new_recommendation: dict[str, Any] | None = None) -> TripRecord | TripCommandRecord | None: ...
