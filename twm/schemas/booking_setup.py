"""``booking_setup`` — deterministic, UI-owned scheduling state for a frozen trip.

Never written by Scout/Meridian/Guide/Atlas and never constrains the itinerary.
It is the single home for the facts a traveler sets to turn a frozen plan into
concrete, prefilled provider searches. There is no trip-level date control —
each bookable entity's date is independent and never propagates. Two concerns:

* ``party`` — the structured adult/child/infant composition booking surfaces
  send as ``traveler_count``. Replaces ``trip_context.traveler_composition``:
  the free-form ``trip_context.num_travelers`` stays a loose planning fact,
  this is the booking-precision value.
* ``search_prefs`` — per-entity date prefill for provider redirect links,
  keyed by the stable id of the enriched ``GET /itinerary`` entity: a stay
  segment (``{trip_id}:stay:{start}:{end}:{slug}``) or a transport timeline
  item (``{trip_id}:{day_number}:{index}``). Pure search convenience; a stale
  entry whose target no longer resolves to a live itinerary entity is simply
  never applied (inert, not an error).

Nothing here regenerates or re-plans the itinerary.
"""

from datetime import date as _date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .flight_search import DepartureMonth
from .trip_context import TravelerComposition

# Re-exported so callers keep one import site for the party value type even
# though its canonical home moved out of trip_context with this change.
__all__ = [
    "TravelerComposition",
    "SearchPrefInput",
    "SearchPrefClearInput",
    "BOOKING_SETUP_BRANCH",
]

BOOKING_SETUP_BRANCH = "booking_setup"

SearchPrefTarget = Literal["stay", "transport"]


class ScheduleDateInput(BaseModel):
    """An exact date XOR a YYYY-MM month window. Never free text — the UI must
    not guess a year from a month label; only a validated exact date or a
    validated month is accepted. Base for ``set_search_pref`` (a single
    entity's search date; removal is ``clear_search_pref``).
    """

    model_config = ConfigDict(extra="forbid")

    date: Optional[_date] = None
    month: Optional[DepartureMonth] = None

    @model_validator(mode="after")
    def validate_precision(self) -> "ScheduleDateInput":
        if self.date is not None and self.month is not None:
            raise ValueError("date and month are mutually exclusive")
        if self.date is None and self.month is None:
            raise ValueError("one of date or month is required")
        return self

    @property
    def precision(self) -> Literal["exact", "month"]:
        return "exact" if self.date is not None else "month"

    def as_stored(self) -> dict[str, Any]:
        if self.date is not None:
            return {"precision": "exact", "date": self.date.isoformat()}
        return {"precision": "month", "month": self.month}


class SearchPrefInput(ScheduleDateInput):
    """``set_search_pref`` payload — a search-date override for one enriched
    ``GET /itinerary`` entity, identified by its stable id (a ``stay_segments``
    entry id or a transport timeline item's ``id``). For a stay the date is the
    check-in; checkout defaults to the segment's night count (editable in the
    drawer).
    """

    target_type: SearchPrefTarget
    target_id: str = Field(min_length=1, max_length=300)


class SearchPrefClearInput(BaseModel):
    """``clear_search_pref`` payload — drop one entity's search-date override
    and revert it to the itinerary-day date (or none, if the trip dates are
    not exact).
    """

    model_config = ConfigDict(extra="forbid")

    target_type: SearchPrefTarget
    target_id: str = Field(min_length=1, max_length=300)
