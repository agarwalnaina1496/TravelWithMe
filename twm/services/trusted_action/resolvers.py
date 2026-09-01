"""SEARCH_REDIRECT partner URL/query-parameter assembly (TWM-131).

Pure string/query assembly only — no outbound HTTP call is ever made here,
and no credential is required to build a safe (non-affiliate-tracked) link.
``twm.schemas.trusted_action.ActionTarget`` is the only place a real
externally-reachable URL gets assembled (fixed base domain + validated path
+ validated query params); this module only decides *which* path and
*which* params a given partner/domain pair gets, then hands them to
``ActionTarget``.

Aviasales (TWM-196) is the one partner with a confirmed, documented deep
link: Travelpayouts' own "Aviasales search form" documentation
(https://support.travelpayouts.com/hc/en-us/articles/8505942823954) gives
``https://search.aviasales.com/flights/?origin_iata=...&destination_iata=...
&depart_date=...&return_date=...&adults=...&children=...&infants=...
&trip_class=...&locale=...&one_way=...`` — IATA-based, not a raw city
label. ``_aviasales_query_params`` below builds exactly that shape, using
``twm.services.airport_resolution.resolve_airport`` so the link always
carries Backend-validated IATA codes when resolution succeeds (never a
raw/guessed city string when a real code is available), and degrades to
the plain place label only if resolution genuinely fails — a less
prefilled but still safe search, never a blocked one.

Judgement call (documented, not fabricated) for every other partner here:
Hotellook/Booking.com/Agoda/redBus/Hostelworld/ixigo transport exact public
deep-link path conventions were not confirmed this session beyond the
shared Travelpayouts ``marker=`` tracking convention already used by
``twm/services/flight_search/aviasales.py``. Rather than guess an
undocumented partner-specific path/param naming scheme, every one of those
partners still uses the same generic ``search`` path plus clearly-named
generic query parameters (``origin``, ``destination``, ``depart_date``,
``return_date``, ``travelers``). Wiring each partner's actual documented
deep-link format is a natural follow-up once each is individually
researched/confirmed.

ixigo stay (TWM-216) is the current exception: its V1 functional redirect
uses ixigo's destination listing path,
``hotels/hotels-in-{destination-slug}``, without date, guest, checkout, or
affiliate tracking prefill.

Tracking parameters:

- ixigo: ``affiliate_id``, sourced from ``TrustedActionSettings.ixigo_affiliate_id``
  (EarnKaro/Cuelinks-style attribution, a separate account from
  Travelpayouts). Omitted entirely when unset — never fails, never a fake
  placeholder.
- aviasales / hotellook / booking_com / agoda (Travelpayouts-brand
  partners): ``marker``, sourced from
  ``TrustedActionSettings.travelpayouts_marker`` — the *same* Travelpayouts
  partner/marker ID the Aviasales adapter already uses for its live-price
  calls (twm/services/flight_search/aviasales.py, same account), injected
  at call-site wiring time rather than duplicated here. flight's
  SEARCH_REDIRECT fallback (TWM-196) therefore shares tracking identity
  with its own live-data path, not with ixigo's separate program.
- redbus: no tracking parameter is wired. redBus has a confirmed
  EarnKaro-based affiliate program, but no deep-link parameter format was
  researched/confirmed this session, so the resolver produces a safe,
  un-tracked search URL only. Wiring redBus tracking is a follow-up, not
  fabricated here.
- hostelworld: no affiliate program was researched this session; un-tracked
  only, same reasoning as redbus.
"""

import re
import unicodedata
from datetime import date
from typing import Optional

from ...schemas.trusted_action import (
    ActionTarget,
    PartnerName,
    TrustedActionCapability,
    TrustedActionDomain,
    TrustedActionRequest,
    TrustedActionText,
    TrustedActionTripType,
)
from ..airport_resolution import resolve_airport
from .settings import TrustedActionSettings

# Generic, domain-scoped search path segment per partner (see module
# docstring — every partner except Aviasales uses the same unconfirmed
# generic "search" path today). aviasales uses its documented
# "flights/" search-form path.
_SEARCH_PATH: dict[PartnerName, str] = {
    "aviasales": "flights/",
    "ixigo": "search",
    "redbus": "search",
    "hotellook": "search",
    "booking_com": "searchresults.html",
    "agoda": "search",
    "hostelworld": "search",
}

_AGODA_DESTINATIONS: dict[str, dict[str, str]] = {
    "goa": {"city": "11304", "city_path": "city/goa-in.html", "label": "Goa"},
}


def resolve_partner_target(
    request: TrustedActionRequest,
    *,
    partner: PartnerName,
    settings: TrustedActionSettings,
) -> ActionTarget:
    """Build the ``ActionTarget`` for a SEARCH_REDIRECT to ``partner``.

    Assumes the caller has already validated that ``partner`` is approved
    for ``request.domain`` (see ``twm.services.trusted_action.calculations``)
    and that every field this function reads is present — readiness is a
    separate, prior concern, not this function's job.
    """

    return ActionTarget(
        partner=partner,
        path=_target_path(
            domain=request.domain,
            destination=request.destination,
            partner=partner,
        ),
        query_params=build_query_params(
            domain=request.domain,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            trip_shape=request.trip_shape,
            traveler_count=request.traveler_count,
            partner=partner,
            settings=settings,
        ),
    )


def _target_path(
    *,
    domain: TrustedActionDomain,
    destination: Optional[str],
    partner: PartnerName,
) -> str:
    if partner == "ixigo" and domain == "stay":
        return f"hotels/hotels-in-{_ixigo_destination_slug(destination or '')}"
    if partner == "agoda" and domain == "stay":
        metadata = _agoda_destination_metadata(destination)
        if metadata is not None and "city" not in metadata:
            return metadata["city_path"]
    return _SEARCH_PATH[partner]


def _ixigo_destination_slug(destination: str) -> str:
    """Build ixigo's destination listing slug for hotels/hotels-in-* URLs."""

    normalized = unicodedata.normalize("NFKD", destination)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.strip().lower())
    return slug.strip("-") or "stay"


def build_query_params(
    *,
    domain: TrustedActionDomain,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    trip_shape: Optional[TrustedActionTripType],
    traveler_count: Optional[int],
    partner: PartnerName,
    settings: TrustedActionSettings,
) -> dict[str, str]:
    """Pure value -> query-parameter mapping, deliberately independent of
    ``TrustedActionRequest`` (plain primitives in, plain dict out) so it can
    be unit-tested directly without instantiating a request schema model,
    per this repo's rule against schema-instantiating unit tests."""

    if partner == "aviasales":
        return _aviasales_query_params(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            trip_shape=trip_shape,
            traveler_count=traveler_count,
            settings=settings,
        )
    if partner == "ixigo" and domain == "stay":
        return {}
    if partner == "booking_com" and domain == "stay":
        return _booking_stay_query_params(
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            traveler_count=traveler_count,
        )
    if partner == "agoda" and domain == "stay":
        return _agoda_stay_query_params(
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            traveler_count=traveler_count,
        )

    params: dict[str, str] = {"domain": domain}
    if origin:
        params["origin"] = origin
    if destination:
        params["destination"] = destination
    if departure_date is not None:
        params["depart_date"] = departure_date.isoformat()
    if return_date is not None:
        params["return_date"] = return_date.isoformat()
    if traveler_count is not None:
        params["travelers"] = str(traveler_count)

    params.update(tracking_params(partner, settings))
    return params


def _booking_stay_query_params(
    *,
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    traveler_count: Optional[int],
) -> dict[str, str]:
    params: dict[str, str] = {
        "ss": destination or "",
        "no_rooms": "1",
        "group_children": "0",
        "selected_currency": "INR",
        "lang": "en-us",
    }
    if departure_date is not None:
        params["checkin"] = departure_date.isoformat()
    if return_date is not None:
        params["checkout"] = return_date.isoformat()
    if traveler_count is not None:
        params["group_adults"] = str(traveler_count)
    return params


def _agoda_stay_query_params(
    *,
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    traveler_count: Optional[int],
) -> dict[str, str]:
    metadata = _agoda_destination_metadata(destination)
    if metadata is None or "city" not in metadata:
        return {}
    params: dict[str, str] = {
        "city": metadata["city"],
        "rooms": "1",
        "children": "0",
        "locale": "en-us",
        "currency": "INR",
        "textToSearch": metadata["label"],
    }
    if departure_date is not None:
        params["checkIn"] = departure_date.isoformat()
    if return_date is not None:
        params["checkOut"] = return_date.isoformat()
    if traveler_count is not None:
        params["adults"] = str(traveler_count)
    return params


def _agoda_destination_metadata(destination: Optional[str]) -> Optional[dict[str, str]]:
    if not destination:
        return None
    return _AGODA_DESTINATIONS.get(_ixigo_destination_slug(destination))


def partner_has_capability(request: TrustedActionRequest, *, partner: PartnerName) -> bool:
    if request.domain == "stay" and partner == "agoda":
        return _agoda_destination_metadata(request.destination) is not None
    return True


def action_capability_metadata(
    request: TrustedActionRequest, *, partner: PartnerName
) -> tuple[TrustedActionCapability, TrustedActionText, TrustedActionText]:
    if request.domain != "stay":
        return ("prefilled_search", "Search options", "Search opens on the selected provider.")

    destination = request.destination or "stays"
    has_dates = request.departure_date is not None and request.return_date is not None
    if partner == "booking_com":
        note = (
            "Destination, dates, room, and traveler count are prefilled on Booking.com."
            if has_dates
            else "Destination search opens on Booking.com; choose exact dates there if needed."
        )
        return ("prefilled_search" if has_dates else "destination_search", "Search Booking.com", note)
    if partner == "agoda":
        metadata = _agoda_destination_metadata(request.destination)
        if metadata is not None and "city" in metadata:
            note = (
                "Known Agoda city metadata lets us prefill this stay search."
                if has_dates
                else "Known Agoda city metadata opens the correct destination search; choose exact dates there if needed."
            )
            return (
                "known_destination_search",
                "Search Agoda",
                note,
            )
        return ("destination_redirect", "Browse Agoda", f"Browse Agoda stays for {destination}.")
    if partner == "ixigo":
        return (
            "destination_redirect",
            "Browse ixigo hotels",
            "ixigo opens the destination hotel page; dates and guests are selected on ixigo.",
        )
    return ("destination_search", "Search stays", "Search opens on the selected provider.")


def _aviasales_query_params(
    *,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    trip_shape: Optional[TrustedActionTripType],
    traveler_count: Optional[int],
    settings: TrustedActionSettings,
) -> dict[str, str]:
    """Aviasales' documented search-form query shape (see module docstring
    for the confirmed source). Backend-owned IATA resolution (TWM-196):
    ``origin_iata``/``destination_iata`` carry a validated code whenever
    ``resolve_airport`` succeeds — never a raw/guessed city string when a
    real code is available. If resolution genuinely fails for a side, this
    falls back to the plain place label under the generic ``origin``/
    ``destination`` keys instead, so the link still degrades to a safe (if
    less prefilled) Aviasales search rather than being blocked entirely.
    """

    params: dict[str, str] = {}

    origin_match = resolve_airport(origin) if origin else None
    if origin_match is not None:
        params["origin_iata"] = origin_match.iata
    elif origin:
        params["origin"] = origin

    destination_match = resolve_airport(destination) if destination else None
    if destination_match is not None:
        params["destination_iata"] = destination_match.iata
    elif destination:
        params["destination"] = destination

    if departure_date is not None:
        params["depart_date"] = departure_date.isoformat()
    if return_date is not None:
        params["return_date"] = return_date.isoformat()
    # trip_shape defaults to one_way on the request (TWM-196); only an
    # explicit round_trip ever sends one_way=false.
    params["one_way"] = "false" if trip_shape == "round_trip" else "true"

    # TrustedActionRequest.traveler_count is a single total, not an
    # adults/children/infants breakdown (no richer per-type contract exists
    # yet — mirrors twm/schemas/flight_search.py's own documented
    # limitation) — mapped to Aviasales' adults param with children/infants
    # explicitly zeroed rather than omitted, since Aviasales' search form
    # treats a missing passenger param as an ambiguous default.
    if traveler_count is not None:
        params["adults"] = str(traveler_count)
        params["children"] = "0"
        params["infants"] = "0"

    # Economy-class default (documented judgement call, not researched
    # further this session) and English locale, matching the rest of this
    # product's copy.
    params["trip_class"] = "0"
    params["locale"] = "en"

    params.update(tracking_params("aviasales", settings))
    return params


_TRAVELPAYOUTS_PARTNERS: frozenset[PartnerName] = frozenset(
    {"aviasales", "hotellook"}
)


def tracking_params(partner: PartnerName, settings: TrustedActionSettings) -> dict[str, str]:
    if partner == "ixigo" and settings.ixigo_affiliate_id:
        return {"affiliate_id": settings.ixigo_affiliate_id}
    if partner in _TRAVELPAYOUTS_PARTNERS and settings.travelpayouts_marker:
        return {"marker": settings.travelpayouts_marker}
    # redbus, hostelworld: no confirmed tracking parameter this session.
    return {}
