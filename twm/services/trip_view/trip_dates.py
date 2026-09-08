"""Compose structured trip dates from the loose ``trip_context.travel_dates``
conversational fact (TWM-217).

A bounded best-effort parse — the same pattern Atlas uses for
``num_travelers``: read it when confidently interpretable, otherwise keep it
verbatim and say so. A year is never guessed when it is not stated. The raw
string in ``trip_context`` is never rewritten — this is a read-time
interpretation only.

Trip dates are read-only after itinerary generation (changing them would
imply regeneration) and independent of booking dates — they only pre-fill
the drawers; per-entity ``search_prefs`` are the booking source of truth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, Optional

DatePrecision = Literal["exact", "month", "none"]

_MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}
_MONTH_ALT = {name[:3]: index for name, index in _MONTHS.items()}

_ISO = r"\d{4}-\d{2}-\d{2}"
_RANGE_SEP = r"\s*(?:-|–|—|to|until|through|thru)\s*"

_ISO_DATE = re.compile(rf"^\s*({_ISO})\s*$")
_ISO_RANGE = re.compile(rf"^\s*({_ISO}){_RANGE_SEP}({_ISO})\s*$", re.IGNORECASE)
_YEAR_MONTH = re.compile(r"^\s*(\d{4})-(0[1-9]|1[0-2])\s*$")
_MONTH_YEAR = re.compile(r"^\s*([A-Za-z]{3,9})\.?\s+(\d{4})\s*$")
# "March 12-17, 2026" / "12-17 March 2026" / "March 12 – March 17 2026"
_DAY_RANGE_YEAR = re.compile(
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*(?:-|–|—|to)\s*(?:[A-Za-z]{3,9}\.?\s+)?(\d{1,2})\s*,?\s*(\d{4})",
)
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_MONTH_DAY_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*,?\s*(\d{4})\b")

_FLEXIBLE = re.compile(r"^\s*(flexible|any\s*time|anytime|not\s*sure|tbd|unsure|open)\b", re.IGNORECASE)


@dataclass(frozen=True)
class TripDates:
    precision: DatePrecision
    departure: Optional[str] = None
    return_: Optional[str] = None
    month: Optional[str] = None
    label: Optional[str] = None
    source: Literal["conversational", "none"] = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "precision": self.precision,
            "departure": self.departure,
            "return": self.return_,
            "month": self.month,
            "label": self.label,
            "source": self.source,
        }


_NONE = TripDates(precision="none")


def _month_index(token: str) -> Optional[int]:
    token = token.lower().rstrip(".")
    return _MONTHS.get(token) or _MONTH_ALT.get(token[:3])


def _iso(value: date) -> str:
    return value.isoformat()


_MONTH_ABBR = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _day_label(value: date) -> str:
    return f"{_MONTH_ABBR[value.month - 1]} {value.day}"


def _from_departure(departure: date, day_count: int) -> TripDates:
    """The itinerary's length is authoritative — a stated end date is only a
    cross-check; if it disagrees, the computed return wins with no warning."""
    nights = max(day_count - 1, 0)
    return_ = departure + timedelta(days=nights)
    if departure.month == return_.month and departure.year == return_.year:
        label = f"{_MONTH_ABBR[departure.month - 1]} {departure.day}–{return_.day}"
    elif departure.year == return_.year:
        label = f"{_day_label(departure)} – {_day_label(return_)}"
    else:
        label = f"{_day_label(departure)}, {departure.year} – {_day_label(return_)}, {return_.year}"
    return TripDates(
        precision="exact",
        departure=_iso(departure),
        return_=_iso(return_),
        label=label,
        source="conversational",
    )


# TWM-223: one branch per (date-precision x day-count-known) combination —
# the composed dates surface is genuinely a small decision table, not a
# god function. Split further only if a real new axis appears.
def compose_trip_dates(trip_context: dict[str, Any], day_count: int) -> TripDates:  # noqa: C901, PLR0912
    raw = trip_context.get("travel_dates") if isinstance(trip_context, dict) else None
    if not isinstance(raw, str):
        return _NONE
    text = raw.strip()
    if not text or _FLEXIBLE.match(text):
        return _NONE

    # --- exact: a single ISO date ---
    match = _ISO_DATE.match(text)
    if match:
        try:
            return _from_departure(date.fromisoformat(match.group(1)), day_count)
        except ValueError:
            return _verbatim(text)

    # --- exact: an ISO range (itinerary length still wins for `return`) ---
    match = _ISO_RANGE.match(text)
    if match:
        try:
            return _from_departure(date.fromisoformat(match.group(1)), day_count)
        except ValueError:
            return _verbatim(text)

    # --- exact: "March 12-17, 2026" style, explicit year ---
    match = _DAY_RANGE_YEAR.search(text)
    if match:
        month = _month_index(match.group(1))
        if month:
            try:
                start = date(int(match.group(4)), month, int(match.group(2)))
                return _from_departure(start, day_count)
            except ValueError:
                pass

    # --- exact: a single "12 March 2026" / "March 12, 2026", explicit year ---
    for pattern, order in ((_DAY_MONTH_YEAR, ("d", "m", "y")), (_MONTH_DAY_YEAR, ("m", "d", "y"))):
        match = pattern.search(text)
        if match:
            groups = dict(zip(order, match.groups()))
            month = _month_index(groups["m"])
            if month:
                try:
                    return _from_departure(date(int(groups["y"]), month, int(groups["d"])), day_count)
                except ValueError:
                    pass

    # --- month: "YYYY-MM" ---
    match = _YEAR_MONTH.match(text)
    if match:
        return _month_result(int(match.group(1)), int(match.group(2)))

    # --- month: "March 2026" ---
    match = _MONTH_YEAR.match(text)
    if match:
        month = _month_index(match.group(1))
        if month:
            return _month_result(int(match.group(2)), month)

    return _verbatim(text)


def _month_result(year: int, month: int) -> TripDates:
    label = date(year, month, 1).strftime("%B %Y")
    return TripDates(precision="month", month=f"{year:04d}-{month:02d}", label=label, source="conversational")


def _verbatim(text: str) -> TripDates:
    # A month name with no year, "mid-March", "spring", etc. — a real signal,
    # but not one we can turn into a calendar value. Never guess the year.
    return TripDates(precision="none", label=f"you mentioned: {text}", source="conversational")
