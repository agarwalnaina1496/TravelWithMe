"""Shared TripContext — the trip_context dict every phase (Scout, Meridian,
Guide, Atlas) reads and writes.

Five keys are fixed because Scout, Meridian, and Guide all read and write
them under these exact names. Everything else keeps a freely chosen
semantic key, so `extra="allow"` preserves Scout's free-form extraction.
Values stay untyped and verbatim for every fixed key, including
``num_travelers`` — same "loose conversational fact" role as ``travel_dates``,
used only for early planning/affordability judgment, never trusted for an
exact booking-time count. See ``TRAVELER_COMPOSITION_KEY`` below for the
structured, Backend-owned counterpart booking surfaces actually read.

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

# BOOKING_DATE_KEY (TWM-201): the canonical post-freeze booking-date
# precision context — Backend-owned, written only by the update_booking_dates
# trip command, never by Scout/Meridian/Guide extraction. Separate from
# travel_dates (a Scout-extracted, untyped, free-form fact used for planning
# context) because this field is structured (exact date XOR month) and
# exists specifically so booking legs can carry departure_date/
# departure_month at the precision the traveler actually confirmed.
BOOKING_DATE_KEY = "booking_dates"

# TRAVELER_COMPOSITION_KEY (TWM-213): the canonical structured traveler
# composition — Backend-owned, written only by the
# update_traveler_composition trip command, never by Scout/Meridian/Guide
# extraction. Separate from num_travelers (a Scout-extracted, untyped,
# free-form fact used for early planning/affordability judgment) for the
# same reason booking_dates is separate from travel_dates: flight fare
# rules and other booking surfaces need an exact adult/child/infant
# breakdown, and LLM chat extraction is not trusted to reliably produce
# that exact a structured value.
TRAVELER_COMPOSITION_KEY = "traveler_composition"

# Every addressable (non-extra) TripContext field — used only to decide
# which fields must not round-trip as an explicit null when unset (see the
# serializer below). Not the same thing as "shared across every phase";
# see DESTINATIONS_KEY's own docstring note above for why it's separate
# from FIXED_KEYS.
_ADDRESSABLE_KEYS = (*FIXED_KEYS, DESTINATIONS_KEY, BOOKING_DATE_KEY, TRAVELER_COMPOSITION_KEY)


class TravelerComposition(BaseModel):
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
    booking_dates: Optional[dict[str, Any]] = None
    traveler_composition: Optional[TravelerComposition] = None

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
