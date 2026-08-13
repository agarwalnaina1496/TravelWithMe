"""Shared TripContext — the trip_context dict every phase (Scout, Meridian,
Guide, Atlas) reads and writes.

Five keys are fixed because Scout, Meridian, and Guide all read and write
them under these exact names. Everything else keeps a freely chosen
semantic key, so `extra="allow"` preserves Scout's free-form extraction.
Values stay untyped and verbatim for every field, fixed or not — a range,
"flexible", a month, "don't know yet", an approximate headcount.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, model_serializer

_FIXED_KEYS = ("origin_city", "num_travelers", "duration_days", "travel_dates", "budget")


class TripContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    origin_city: Optional[Any] = None
    num_travelers: Optional[Any] = None
    duration_days: Optional[Any] = None
    travel_dates: Optional[Any] = None
    budget: Optional[Any] = None

    @model_serializer(mode="wrap")
    def _omit_unset_fixed_keys(self, handler: Any) -> dict[str, Any]:
        # The five fixed keys default to None purely to make them addressable
        # fields; unlike extra keys (which simply don't exist unless
        # supplied), an unset fixed key must not round-trip as an explicit
        # null. An extra key explicitly set to null (e.g. Guide clearing
        # start_date) is untouched here.
        data = handler(self)
        return {
            key: value
            for key, value in data.items()
            if value is not None or key not in _FIXED_KEYS
        }
