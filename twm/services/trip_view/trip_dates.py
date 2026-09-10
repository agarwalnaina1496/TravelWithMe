"""Compose structured trip dates from ``trip_context.travel_dates`` (TWM-217,
TWM-227).

Scout / Meridian / Guide record ``travel_dates`` in a plain form — an ISO
date (``2026-11-03``), an ISO range, ``YYYY-MM``, a bare ``D Month`` when the
traveler gave no year, or ``Month`` — and keep the traveler's own wording
only when the timing is genuinely non-specific ("flexible", a season). This
module parses that clean form, resolves an omitted year, and formats a
display label. The regex layer below also still tolerates looser prose
(ordinals, "12-17 March 2026", month names) as a safety net for a value an
agent did not normalise and for direct API tests. A value it cannot resolve
renders verbatim. The raw string in ``trip_context`` is never rewritten —
this is a read-time interpretation only.

Year resolution (TWM-227): when the traveler states an exact day+month with
no year, the omitted year is resolved to the current year *only when the
resulting date is today-or-future* — the near-future occurrence is how
everyone reads "26-28 Sept". A range whose end month precedes its start
month ("Dec 30 - Jan 2") spans the year boundary (current-year start,
next-year end). A bare day/month whose current-year date is already **past**
is left unresolved (clean label, precision ``none``); nothing asks the
traveler for the year — the booking drawer is where they set an exact date.
The composer never guesses across a year boundary for that ambiguous case.
Any text it cannot resolve renders verbatim, with no prefix.

Ordinal day suffixes are tolerated: "26th - 28th Sept" reads the same as
"26-28 Sept". Only the "26th" -> "26" rewrite happens before matching;
nothing else in the string is normalised, and a value that still does not
match a recognised shape ("late October", "the week of Diwali") is left
verbatim exactly as the traveler wrote it.

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
# "26th" -> "26", "1st" -> "1" — a day number, not a 4-digit year.
_ORDINAL_SUFFIX = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)

_ISO_DATE = re.compile(rf"^\s*({_ISO})\s*$")
_ISO_RANGE = re.compile(rf"^\s*({_ISO}){_RANGE_SEP}({_ISO})\s*$", re.IGNORECASE)
_YEAR_MONTH = re.compile(r"^\s*(\d{4})-(0[1-9]|1[0-2])\s*$")
_MONTH_YEAR = re.compile(r"^\s*([A-Za-z]{3,9})\.?\s+(\d{4})\s*$")
# month-first range: "March 12-17, 2026" / "March 12 – March 17 2026"
_DAY_RANGE_YEAR = re.compile(
    r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*(?:-|–|—|to)\s*(?:[A-Za-z]{3,9}\.?\s+)?(\d{1,2})\s*,?\s*(\d{4})",
)
# day-first range: "12-17 March 2026" / "12 to 17 March 2026" — groups are
# (start day, end day, month, year). Without this the single-date matcher
# below grabs the *end* day out of the range.
_DAY_RANGE_YEAR_DM = re.compile(
    rf"(\d{{1,2}}){_RANGE_SEP}(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?\s*,?\s*(\d{{4}})", re.IGNORECASE
)
_DAY_MONTH_YEAR = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_MONTH_DAY_YEAR = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*,?\s*(\d{4})\b")

# TWM-227: yearless forms, matched against the whole stripped string so
# free-text ("sometime in March", "mid-March") never trips them. The year is
# supplied by _parse_yearless against `today`, never by the pattern.
_YL_RANGE_DAY_MONTH = re.compile(rf"^(\d{{1,2}}){_RANGE_SEP}(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?$", re.IGNORECASE)
_YL_RANGE_MONTH_DAY = re.compile(rf"^([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}}){_RANGE_SEP}(\d{{1,2}})$", re.IGNORECASE)
_YL_SINGLE_DAY_MONTH = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3,9})\.?$", re.IGNORECASE)
_YL_SINGLE_MONTH_DAY = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})$", re.IGNORECASE)
_YL_CROSS_DAY_MONTH = re.compile(
    rf"^(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?{_RANGE_SEP}(\d{{1,2}})\s+([A-Za-z]{{3,9}})\.?$", re.IGNORECASE
)
_YL_CROSS_MONTH_DAY = re.compile(
    rf"^([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}}){_RANGE_SEP}([A-Za-z]{{3,9}})\.?\s+(\d{{1,2}})$", re.IGNORECASE
)
_YL_BARE_MONTH = re.compile(r"^([A-Za-z]{3,9})\.?$", re.IGNORECASE)

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


def compose_trip_dates(
    trip_context: dict[str, Any], day_count: int, *, today: Optional[date] = None
) -> TripDates:
    """Read ``trip_context.travel_dates`` at the precision the traveler gave
    it — exact departure, a month, or unresolved — trying the parses from
    most specific to least and keeping the raw string when none fit."""
    raw = trip_context.get("travel_dates") if isinstance(trip_context, dict) else None
    if not isinstance(raw, str):
        return _NONE
    original = raw.strip()
    if not original or _FLEXIBLE.match(original):
        return _NONE
    today = today or date.today()
    # Match against an ordinal-stripped copy; the verbatim fallback still
    # shows the traveler's own wording.
    text = _ORDINAL_SUFFIX.sub(r"\1", original)

    return (
        _parse_exact_dated(text, day_count)
        or _parse_bare_month(text)
        or _parse_yearless(text, day_count, today)
        or _verbatim(original)
    )


def _parse_exact_dated(text: str, day_count: int) -> Optional[TripDates]:
    """A value that pins an exact departure: an ISO date/range, or a
    day + month + year in any common ordering. ``None`` when nothing matches
    (a matched-but-impossible calendar date included — the caller falls
    through to the looser parses and then to verbatim)."""
    for pattern in (_ISO_DATE, _ISO_RANGE):
        match = pattern.match(text)
        if match:
            try:
                return _from_departure(date.fromisoformat(match.group(1)), day_count)
            except ValueError:
                return None

    # (month token, start-day, year) pulled from whichever explicit-year
    # shape matched — month-first range, day-first range, then single date.
    for match, month_g, day_g, year_g in _explicit_year_matches(text):
        month = _month_index(match.group(month_g))
        if month:
            try:
                return _from_departure(date(int(match.group(year_g)), month, int(match.group(day_g))), day_count)
            except ValueError:
                continue
    return None


def _explicit_year_matches(text: str):
    if (m := _DAY_RANGE_YEAR.search(text)):        # "March 12-17, 2026"
        yield m, 1, 2, 4
    if (m := _DAY_RANGE_YEAR_DM.search(text)):     # "12-17 March 2026"
        yield m, 3, 1, 4
    if (m := _DAY_MONTH_YEAR.search(text)):        # "12 March 2026"
        yield m, 2, 1, 3
    if (m := _MONTH_DAY_YEAR.search(text)):        # "March 12, 2026"
        yield m, 1, 2, 3


def _parse_bare_month(text: str) -> Optional[TripDates]:
    if (match := _YEAR_MONTH.match(text)):         # "2026-03"
        return _month_result(int(match.group(1)), int(match.group(2)))
    if (match := _MONTH_YEAR.match(text)):         # "March 2026"
        month = _month_index(match.group(1))
        if month:
            return _month_result(int(match.group(2)), month)
    return None


def _month_result(year: int, month: int) -> TripDates:
    label = date(year, month, 1).strftime("%B %Y")
    return TripDates(precision="month", month=f"{year:04d}-{month:02d}", label=label, source="conversational")


def _verbatim(text: str) -> TripDates:
    # A season, "mid-March", an unrecognizable phrase — a real signal, but not
    # one we can turn into a calendar value. Rendered as the traveler wrote it.
    return TripDates(precision="none", label=text, source="conversational")


def recap_label(trip_context: dict[str, Any], *, today: Optional[date] = None) -> Optional[str]:
    """A short human label for the "When" context-recap row — decoupled from
    however ``travel_dates`` is stored so the recap stays readable whether the
    agent wrote ``2026-11-03`` or the traveler's own prose. ``None`` when the
    timing is unset (the caller falls back to the raw value)."""
    composed = compose_trip_dates(trip_context, 0, today=today)
    if composed.precision == "exact" and composed.departure:
        parsed = date.fromisoformat(composed.departure)
        return f"{parsed.day} {parsed:%b %Y}"
    return composed.label


def _yearless_label(month: int, day_start: int, day_end: Optional[int] = None) -> str:
    label = f"{_MONTH_ABBR[month - 1]} {day_start}"
    return f"{label}–{day_end}" if day_end is not None else label


def _resolve_current_year(month: int, day: int, today: date) -> Optional[int]:
    """The omitted year is this year when that lands today-or-later; a
    day/month already past this year is ambiguous — left for Guide to ask."""
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    return today.year if candidate >= today else None


def _parse_yearless(text: str, day_count: int, today: date) -> Optional[TripDates]:
    """A day+month (or day-range, or two-month range) with no year stated.
    Returns ``None`` when nothing matched, so the caller falls back to
    verbatim."""
    for pattern, month_first in ((_YL_CROSS_MONTH_DAY, True), (_YL_CROSS_DAY_MONTH, False)):
        match = pattern.match(text)
        if match:
            return _two_month_range(match.groups(), month_first, text, day_count, today)

    for pattern, month_first in ((_YL_RANGE_MONTH_DAY, True), (_YL_RANGE_DAY_MONTH, False)):
        match = pattern.match(text)
        if match:
            groups = match.groups()
            month = _month_index(groups[0] if month_first else groups[2])
            start = int(groups[1] if month_first else groups[0])
            end = int(groups[2] if month_first else groups[1])
            return _resolve_yearless_start(month, start, day_count, today, _yearless_label(month, start, end))

    for pattern, month_first in ((_YL_SINGLE_MONTH_DAY, True), (_YL_SINGLE_DAY_MONTH, False)):
        match = pattern.match(text)
        if match:
            month = _month_index(match.group(1 if month_first else 2))
            day = int(match.group(2 if month_first else 1))
            return _resolve_yearless_start(month, day, day_count, today, _yearless_label(month, day))

    match = _YL_BARE_MONTH.match(text)
    if match:
        month = _month_index(match.group(1))
        if month:
            return _month_result(today.year, month) if month >= today.month else _verbatim(text)

    return None


def _resolve_yearless_start(
    month: Optional[int], day_start: int, day_count: int, today: date, unresolved_label: str
) -> Optional[TripDates]:
    """Resolve an omitted year to the current year when the start is
    today-or-future; leave a start already past this year unresolved (clean
    label, ``precision: none``) for Guide to clarify — never a next-year guess."""
    if not month:
        return None
    year = _resolve_current_year(month, day_start, today)
    if year is None:
        return TripDates(precision="none", label=unresolved_label, source="conversational")
    return _from_departure(date(year, month, day_start), day_count)


def _two_month_range(
    groups: tuple[str, ...], month_first: bool, text: str, day_count: int, today: date
) -> Optional[TripDates]:
    """A range with a month on both sides ("Dec 30 - Jan 2", "Feb 26 - Mar 2").
    Only a range whose end month precedes its start month genuinely spans the
    year boundary — that one anchors the start to the current year (rolled
    forward if past) with no question, since it is unambiguous. Any other
    two-month range is an ordinary same-year range and goes through the same
    resolve-if-future / leave-for-Guide-if-past path as every other yearless
    start."""
    if month_first:
        start_month, start_day = _month_index(groups[0]), int(groups[1])
        end_month = _month_index(groups[2])
    else:
        start_day, start_month = int(groups[0]), _month_index(groups[1])
        end_month = _month_index(groups[3])
    if not start_month or not end_month:
        return None
    if end_month >= start_month:
        return _resolve_yearless_start(start_month, start_day, day_count, today, text)
    try:
        start = date(today.year, start_month, start_day)
        # Deliberate: a genuine cross-year phrase ("Dec 30 - Jan 2") is
        # unambiguous even when its current-year start is already past —
        # "the next one" is the only sensible read, so roll it forward
        # rather than leave it unresolved. Not the same as the single-date
        # ambiguity above, which we never guess across a boundary.
        if start < today:
            start = date(today.year + 1, start_month, start_day)
    except ValueError:
        return None
    return _from_departure(start, day_count)
