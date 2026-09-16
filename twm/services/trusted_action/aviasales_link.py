"""Aviasales's real, browser-verified deep-link shape (TWM-230 Increment
2d correction).

Split out of ``resolvers.py`` to keep that module under the 600-line
module-size cap (TWM-223) -- a cohesive, single-partner extraction, not a
layering change.

The confirmed shape (Travelpayouts' "Aviasales affiliate links" article,
https://support.travelpayouts.com/hc/en-us/articles/5711895629714) packs
origin/date/destination/passengers into a compact *path* segment:
``https://www.aviasales.com/search/{ORIGIN_IATA}{DDMM}{DEST_IATA}
[{RETURN_DDMM}]{PASSENGERS}`` -- not the ``origin_iata=``/
``destination_iata=`` query-param shape an earlier (different)
Travelpayouts support article documented, which this story built against
first and then browser-verified broken: it dropped every param and
redirected to the .ru marketing homepage instead of showing results.

Pure helpers only -- no tracking/settings dependency here; the caller
(``resolvers.build_query_params``) appends ``tracking_params(...)`` itself,
same as every other partner.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from ...schemas.trusted_action import TrustedActionTripType

Party = tuple[int, int, int]  # (adults, children, infants)


def aviasales_passenger_suffix(occupancy: Optional[Party]) -> str:
    """The passenger-count suffix Aviasales' compact search code needs,
    positionally encoded: the adults digit is always present; the
    children digit is present only if there are children *or* infants (a
    trailing infant digit needs a children placeholder before it, even
    when children is 0); the infants digit is present only if there are
    infants. E.g. 1 adult alone -> "1"; 1 adult + 1 infant -> "101"; 2
    adults + 1 child -> "21". Economy class only (documented judgement
    call, not researched further this session) -- no class letter prefix.
    """

    adults, children, infants = occupancy if occupancy is not None else (1, 0, 0)
    digits = str(max(1, adults))
    if children > 0 or infants > 0:
        digits += str(children)
    if infants > 0:
        digits += str(infants)
    return digits


def aviasales_route_segment(
    origin_iata: str,
    destination_iata: str,
    departure_date: Optional[date],
    return_date: Optional[date],
    trip_shape: Optional[TrustedActionTripType],
) -> str:
    segment = origin_iata
    if departure_date is not None:
        segment += departure_date.strftime("%d%m")
    segment += destination_iata
    if trip_shape == "round_trip" and return_date is not None and departure_date is not None:
        segment += return_date.strftime("%d%m")
    return segment


def aviasales_form_params(
    *,
    origin_iata: Optional[str],
    destination_iata: Optional[str],
    departure_date: Optional[date],
    occupancy: Optional[Party],
) -> dict[str, str]:
    """The ``params=`` query key for the dateless pre-filled *search
    form* fallback (root path) -- the compact search-*results* path
    (``aviasales_route_segment`` above, used in the URL path) 404s
    without a date, browser-verified, so a resolved route with no date
    degrades to this instead of a broken results page. Empty when the
    route itself doesn't resolve -- never a guessed/partial code.
    """

    if origin_iata is None or destination_iata is None or departure_date is not None:
        return {}
    return {"params": f"{origin_iata}{destination_iata}{aviasales_passenger_suffix(occupancy)}"}
