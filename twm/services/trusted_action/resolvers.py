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

ixigo (train) and redBus (bus) each have a confirmed, real deep-link shape
(TWM-230 Increment 2, browser-verified) built when their inputs resolve
specifically enough; otherwise both degrade to a plain, generic search
surface — never a guessed partner-specific path/param scheme.

Stay redirects (TWM-216) now use the confirmed capability matrix:
Booking.com gets its native ``searchresults.html`` query shape, and ixigo
gets its destination hotel listing path
``hotels/hotels-in-{destination-slug}``. None of these direct stay links
claims affiliate tracking unless a real tracking param is present.

Agoda was dropped entirely (TWM-230 Increment 2b): its search needs an
internal numeric city ID, not a place name, and the only entry TWM ever
held was a single hand-curated one for Goa -- the exact hardcoded
place->ID anti-pattern this story rejected elsewhere (the original
``_IXIGO_STATION_CODES`` dict, ixigo-as-a-bus-partner). Revisit only if a
real city-ID resolution mechanism turns up (e.g. Travelpayouts' own
deep-link generator, if it resolves a destination automatically).

Tracking parameters:

- ixigo: ``affiliate_id``, sourced from ``TrustedActionSettings.ixigo_affiliate_id``
  (EarnKaro/Cuelinks-style attribution, a separate account from
  Travelpayouts). Omitted entirely when unset — never fails, never a fake
  placeholder.
- aviasales (confirmed Travelpayouts redirect shape): ``marker``, sourced
  from ``TrustedActionSettings.travelpayouts_marker`` — the *same*
  Travelpayouts partner/marker ID the Aviasales adapter already uses for
  its live-price calls (twm/services/flight_search/aviasales.py, same
  account), injected at call-site wiring time rather than duplicated here.
  flight's SEARCH_REDIRECT fallback (TWM-196) therefore shares tracking
  identity with its own live-data path, not with ixigo's separate program.
- redbus: no tracking parameter is wired yet. redBus has a confirmed
  EarnKaro-based affiliate program, but wiring it needs submitting the
  built link through EarnKaro's own link-generation tool (a "Shape B"
  link-wrapping integration, TWM_Docs/BOOKING_HANDOFF.md) — appending a
  param to redBus's own URL does not earn commission. Not fabricated here.
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
from ..airport_resolution import AirportResolution, resolve_airport
from ..station_resolution import resolve_station
from .settings import TrustedActionSettings

# Generic, domain-scoped search path segment per partner. Aviasales,
# ixigo trains, and redBus use confirmed public deep-link shapes when the
# inputs are specific enough; otherwise they degrade to these safe search
# surfaces without pretending the route was prefilled.
_SEARCH_PATH: dict[PartnerName, str] = {
    "aviasales": "flights/",
    "ixigo": "trains",
    "redbus": "bus-tickets",
    "booking_com": "searchresults.html",
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
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
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
            party=(
                (request.traveler_party.adults, request.traveler_party.children, request.traveler_party.infants)
                if request.traveler_party is not None
                else None
            ),
            partner=partner,
            settings=settings,
        ),
    )


def _target_path(
    *,
    domain: TrustedActionDomain,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    partner: PartnerName,
) -> str:
    if partner == "ixigo" and domain == "stay":
        return f"hotels/hotels-in-{_ixigo_destination_slug(destination or '')}"
    if partner == "ixigo" and domain == "train":
        origin_station = _ixigo_station_code(origin)
        destination_station = _ixigo_station_code(destination)
        if origin_station is not None and destination_station is not None and departure_date is not None:
            return (
                "trains/search-pwa/from/"
                f"{origin_station}/to/{destination_station}/{departure_date.strftime('%d-%m-%Y')}"
            )
    if partner == "redbus" and domain == "bus" and origin and destination:
        return f"bus-tickets/{_redbus_city_slug(origin)}-to-{_redbus_city_slug(destination)}"
    return _SEARCH_PATH[partner]


def _is_scheduled_airport(resolution: Optional[AirportResolution]) -> bool:
    """Whether ``resolution`` names a real, currently-scheduled commercial
    airport -- not merely *an* airport (TWM-230 plausibility hardening).

    ``resolve_airport``'s "ourairports" match path already ties its
    confidence tier directly to the dataset's own ``scheduled_service``
    flag (``confidence="high" if best.scheduled_service else "low"``) --
    a small/non-scheduled airstrip resolves but is tagged ``"low"``. Using
    an airstrip's IATA code in a live Aviasales search is the same
    overclaim this story fixed for ixigo train stations: a real code that
    still returns nothing useful. Curated overrides/fallback are trusted
    regardless of their tag -- they're hand-vetted major-city airports, not
    a dataset-ranked candidate, so their "low" tag doesn't carry this
    meaning.
    """

    if resolution is None:
        return False
    if resolution.source != "ourairports":
        return True
    return resolution.confidence == "high"


def _ixigo_destination_slug(destination: str) -> str:
    """Build ixigo's destination listing slug for hotels/hotels-in-* URLs."""

    normalized = unicodedata.normalize("NFKD", destination)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.strip().lower())
    return slug.strip("-") or "stay"


def _ixigo_station_code(place: Optional[str]) -> Optional[str]:
    """The IRCTC station code ixigo's train search-form deep link needs,
    resolved from the bundled Indian Railways station dataset (TWM-230) --
    never a hand-maintained place -> code table. Returns ``None`` (never a
    guessed code) when the dataset cannot confidently place ``place``."""

    resolution = resolve_station(place)
    return resolution.code if resolution is not None else None


def _redbus_city_slug(place: str) -> str:
    return _ixigo_destination_slug(place)


Party = tuple[int, int, int]  # (adults, children, infants)


def _occupancy(party: Optional[Party], traveler_count: Optional[int]) -> Optional[Party]:
    """The party to fill an occupancy form with. Prefer the structured
    ``party`` when the caller has one; otherwise fall back to the single
    total as an all-adults party. ``None`` when neither is known.
    """
    if party is not None:
        return party
    if traveler_count is not None:
        return (traveler_count, 0, 0)
    return None


def build_query_params(
    *,
    domain: TrustedActionDomain,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    trip_shape: Optional[TrustedActionTripType],
    traveler_count: Optional[int],
    party: Optional[Party] = None,
    partner: PartnerName,
    settings: TrustedActionSettings,
) -> dict[str, str]:
    """Pure value -> query-parameter mapping, deliberately independent of
    ``TrustedActionRequest`` (plain primitives in, plain dict out) so it can
    be unit-tested directly without instantiating a request schema model,
    per this repo's rule against schema-instantiating unit tests.

    ``party`` is ``(adults, children, infants)``; ``traveler_count`` is the
    single-total fallback for callers without a structured party.
    """

    occupancy = _occupancy(party, traveler_count)

    if partner == "aviasales":
        return _aviasales_query_params(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            trip_shape=trip_shape,
            occupancy=occupancy,
            settings=settings,
        )
    if partner == "ixigo" and domain == "stay":
        return {}
    if partner == "booking_com" and domain == "stay":
        return _booking_stay_query_params(
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            occupancy=occupancy,
        )
    if partner == "redbus" and domain == "bus":
        return _redbus_bus_query_params(departure_date=departure_date)

    if partner == "ixigo" and domain == "train":
        return _ixigo_train_query_params(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            settings=settings,
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


def _redbus_bus_query_params(*, departure_date: Optional[date]) -> dict[str, str]:
    if departure_date is None:
        return {}
    return {"onward": departure_date.strftime("%d-%b-%Y")}


def _ixigo_train_query_params(
    *,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    settings: TrustedActionSettings,
) -> dict[str, str]:
    if (
        _ixigo_station_code(origin) is not None
        and _ixigo_station_code(destination) is not None
        and departure_date is not None
    ):
        return tracking_params("ixigo", settings)
    params: dict[str, str] = {"domain": "train"}
    if origin:
        params["origin"] = origin
    if destination:
        params["destination"] = destination
    params.update(tracking_params("ixigo", settings))
    return params


def _booking_stay_query_params(
    *,
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    occupancy: Optional[Party],
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
    if occupancy is not None:
        adults, children, infants = occupancy
        # Booking.com hotel search has no infant concept — a lap infant is
        # not a declared occupant. Adults + children only. Booking.com
        # accepts group_children without per-child age params (it prompts
        # for ages on the results page); we never guess an age.
        params["group_adults"] = str(max(1, adults))
        params["group_children"] = str(children + infants)
    return params


def partner_has_capability(request: TrustedActionRequest, *, partner: PartnerName) -> bool:
    return True


def action_capability_metadata(
    request: TrustedActionRequest, *, partner: PartnerName
) -> tuple[TrustedActionCapability, TrustedActionText, TrustedActionText]:
    if request.domain != "stay":
        has_route = request.origin is not None and request.destination is not None
        has_date = request.departure_date is not None
        if partner == "aviasales":
            has_prefill = (
                has_route
                and has_date
                and _is_scheduled_airport(resolve_airport(request.origin))
                and _is_scheduled_airport(resolve_airport(request.destination))
            )
            note = (
                "Route, date, and traveler count open on Aviasales when airport resolution succeeds."
                if has_prefill
                else "Aviasales opens this route search; choose exact dates there if needed."
            )
            return ("prefilled_search" if has_prefill else "destination_search", "Search Aviasales", note)
        if partner == "ixigo":
            has_prefill = (
                _ixigo_station_code(request.origin) is not None
                and _ixigo_station_code(request.destination) is not None
                and has_date
            )
            note = (
                "Station codes and date open prefilled on ixigo trains; confirm schedule, seats, and fare on ixigo."
                if has_prefill
                else "ixigo trains opens as a search surface; choose the exact stations and date there."
            )
            return ("prefilled_search" if has_prefill else "destination_search", "Search ixigo trains", note)
        if partner == "redbus":
            has_prefill = has_route and has_date
            note = (
                "Route and date open prefilled on redBus; confirm seats and fare on redBus."
                if has_prefill
                else "redBus opens as a search surface; choose the route and date there."
            )
            return ("prefilled_search" if has_prefill else "destination_search", "Search redBus", note)
        return ("destination_search", "Search options", "Search opens on the selected provider.")

    has_dates = request.departure_date is not None and request.return_date is not None
    if partner == "booking_com":
        note = (
            "Destination, dates, room, and traveler count are prefilled on Booking.com."
            if has_dates
            else "Destination search opens on Booking.com; choose exact dates there if needed."
        )
        return ("prefilled_search" if has_dates else "destination_search", "Search Booking.com", note)
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
    occupancy: Optional[Party],
    settings: TrustedActionSettings,
) -> dict[str, str]:
    """Aviasales' documented search-form query shape (see module docstring
    for the confirmed source). Backend-owned IATA resolution (TWM-196):
    ``origin_iata``/``destination_iata`` carry a validated code whenever
    ``resolve_airport`` succeeds **and names a real, currently-scheduled
    airport** (TWM-230 plausibility hardening — a resolved-but-unscheduled
    airstrip's IATA code is as useless to a live flight search as no code
    at all; see ``_is_scheduled_airport``) — never a raw/guessed city
    string when a real, useful code is available. If resolution genuinely
    fails, or resolves to a non-scheduled airstrip, this falls back to the
    plain place label under the generic ``origin``/``destination`` keys
    instead, so the link still degrades to a safe (if less prefilled)
    Aviasales search rather than being blocked entirely.
    """

    params: dict[str, str] = {}

    origin_match = resolve_airport(origin) if origin else None
    if _is_scheduled_airport(origin_match):
        params["origin_iata"] = origin_match.iata
    elif origin:
        params["origin"] = origin

    destination_match = resolve_airport(destination) if destination else None
    if _is_scheduled_airport(destination_match):
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

    # Aviasales' search form treats a missing passenger param as an ambiguous
    # default, so all three are always sent when the party is known. A caller
    # with only a single total (no structured party) lands here as an
    # all-adults occupancy.
    if occupancy is not None:
        adults, children, infants = occupancy
        params["adults"] = str(max(1, adults))
        params["children"] = str(children)
        params["infants"] = str(infants)

    # Economy-class default (documented judgement call, not researched
    # further this session) and English locale, matching the rest of this
    # product's copy.
    params["trip_class"] = "0"
    params["locale"] = "en"

    params.update(tracking_params("aviasales", settings))
    return params


_TRAVELPAYOUTS_PARTNERS: frozenset[PartnerName] = frozenset({"aviasales"})


def tracking_params(partner: PartnerName, settings: TrustedActionSettings) -> dict[str, str]:
    if partner == "ixigo" and settings.ixigo_affiliate_id:
        return {"affiliate_id": settings.ixigo_affiliate_id}
    if partner in _TRAVELPAYOUTS_PARTNERS and settings.travelpayouts_marker:
        return {"marker": settings.travelpayouts_marker}
    # redbus, booking_com: no wired tracking parameter -- both need a
    # link-wrapping integration, not a param (see
    # TWM_Docs/BOOKING_HANDOFF.md).
    return {}
