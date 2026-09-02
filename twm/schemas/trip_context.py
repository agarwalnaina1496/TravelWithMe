"""Shared TripContext — the trip_context dict every phase (Scout, Meridian,
Guide, Atlas) reads and writes.

Five keys are fixed because Scout, Meridian, and Guide all read and write
them under these exact names. Everything else keeps a freely chosen
semantic key, so `extra="allow"` preserves Scout's free-form extraction.
Values stay untyped and verbatim for every fixed key, including
``num_travelers`` — same "loose conversational fact" role as ``travel_dates``,
used only for early planning/affordability judgment, never trusted for an
exact booking-time count. The structured, Backend-owned booking-precision
counterparts (calendar anchor, adult/child/infant party) live in the
``booking_setup`` state branch, never in trip_context — nothing an agent
extracts belongs there.

`destinations` is a second, separately-named field — not one of the five
`FIXED_KEYS` — because it isn't genuinely 3-way shared the same way: only
Guide gates on it as an input (Meridian produces destinations, never
consumes one; Scout doesn't reference it). It's the one canonical
"what's the destination" signal for both entry paths — Backend writes it
directly when a Discover-path option is selected (`select_destination`),
Guide extracts it from the traveler's own message on the known-destination
path — so every downstream reader (Guide's own gate, plan-freeze, the
trip summary/recap surfaces) has exactly one field to check, never a
per-path fallback.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

# Individual key constants so callers that need one specific key (instead
# of the full FIXED_KEYS tuple) never re-hardcode the literal.
ORIGIN_CITY_KEY = "origin_city"
NUM_TRAVELERS_KEY = "num_travelers"
TRIP_DURATION_KEY = "trip_duration"
TRAVEL_DATES_KEY = "travel_dates"
BUDGET_KEY = "budget"

# Public so other schemas (e.g. Guide's awaiting enum) can derive their own
# fixed-key references from this single source instead of re-hardcoding it.
FIXED_KEYS = (
    ORIGIN_CITY_KEY,
    NUM_TRAVELERS_KEY,
    TRIP_DURATION_KEY,
    TRAVEL_DATES_KEY,
    BUDGET_KEY,
)

DESTINATIONS_KEY = "destinations"

# Every addressable (non-extra) TripContext field — used only to decide
# which fields must not round-trip as an explicit null when unset (see the
# serializer below). Not the same thing as "shared across every phase";
# see DESTINATIONS_KEY's own docstring note above for why it's separate
# from FIXED_KEYS.
_ADDRESSABLE_KEYS = (*FIXED_KEYS, DESTINATIONS_KEY)


class TravelerComposition(BaseModel):
    """Structured adult/child/infant party. Lives on the ``booking_setup``
    state branch (written by the ``set_party`` command), never in
    trip_context — the free-form ``num_travelers`` fact is the only traveler
    count an agent ever touches. Kept in this module as a shared value type
    so schema imports have one stable home.
    """

    model_config = ConfigDict(extra="forbid")

    adults: int = Field(ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def validate_bounds(self) -> "TravelerComposition":
        if self.total > 9:
            raise ValueError("total travelers must not exceed 9")
        if self.infants > self.adults:
            raise ValueError("infants cannot exceed adults")
        return self

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


class TripContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    origin_city: Optional[Any] = None
    num_travelers: Optional[Any] = None
    trip_duration: Optional[Any] = None
    travel_dates: Optional[Any] = None
    budget: Optional[Any] = None
    destinations: Optional[list[str]] = None

    @model_serializer(mode="wrap")
    def _omit_unset_fixed_keys(self, handler: Any) -> dict[str, Any]:
        # These addressable keys default to None purely to make them
        # addressable fields; unlike extra keys (which simply don't exist
        # unless supplied), an unset one must not round-trip as an explicit
        # null. An extra key explicitly set to null (e.g. an agent clearing
        # a free-form fact it previously extracted) is untouched here.
        data = handler(self)
        return {
            key: value
            for key, value in data.items()
            if value is not None or key not in _ADDRESSABLE_KEYS
        }
