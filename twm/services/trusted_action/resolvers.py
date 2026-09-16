"""SEARCH_REDIRECT partner URL/query-parameter assembly (TWM-131).

Pure string/query assembly only — no outbound HTTP call is ever made here,
and no credential is required to build a safe (non-affiliate-tracked) link.
``twm.schemas.trusted_action.ActionTarget`` is the only place a real
externally-reachable URL gets assembled (fixed base domain + validated path
+ validated query params); this module only decides *which* path and
*which* params a given partner/domain pair gets, then hands them to
``ActionTarget``.

Aviasales (TWM-196, corrected TWM-230 Increment 2d) has a confirmed, real
deep link: Travelpayouts' "Aviasales affiliate links" article
(https://support.travelpayouts.com/hc/en-us/articles/5711895629714) gives
``https://www.aviasales.com/search/{ORIGIN_IATA}{DDMM}{DEST_IATA}
[{RETURN_DDMM}]{PASSENGERS}`` — IATA-based, route/date/passengers packed
into a compact *path* segment, browser-verified. An earlier support
article documented a ``search.aviasales.com/flights/?origin_iata=...``
query-param shape that this story built against first; browser-verifying
it this session showed it drops every param and redirects to the
marketing homepage — corrected to the real shape in
``_target_path``/``_aviasales_query_params`` below, using
``twm.services.airport_resolution.resolve_airport`` so the link always
carries Backend-validated IATA codes when resolution succeeds, and
degrades to a dateless pre-filled *form* (route only) or the bare
homepage when resolution or the date is missing — never a guessed code,
never a broken results page.

ixigo (train, flight) and redBus (bus) each have a confirmed, real
deep-link shape (TWM-230 Increment 2/2c, browser-verified) built when
their inputs resolve specifically enough; otherwise each degrades to a
plain, generic search surface — never a guessed partner-specific
path/param scheme. ixigo flight is a second SEARCH_REDIRECT alternative
alongside Aviasales (TWM-196's live CHECK_PRICES path stays
Aviasales-only); it uses the same IATA-code resolution as Aviasales
(``_scheduled_airport_iata``), just its own confirmed query shape
(``https://www.ixigo.com/search/result/flight?from=...&to=...&date=
DDMMYYYY&...``).

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
from . import aviasales_link
from .settings import TrustedActionSettings

# Generic, domain-scoped search path segment per partner. Aviasales,
# ixigo trains, and redBus use confirmed public deep-link shapes when the
# inputs are specific enough; otherwise they degrade to these safe search
# surfaces without pretending the route was prefilled.
_SEARCH_PATH: dict[PartnerName, str] = {
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

    party = (
        (request.traveler_party.adults, request.traveler_party.children, request.traveler_party.infants)
        if request.traveler_party is not None
        else None
    )
    return ActionTarget(
        partner=partner,
        path=_target_path(
            domain=request.domain,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            trip_shape=request.trip_shape,
            traveler_count=request.traveler_count,
            party=party,
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
            party=party,
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
    return_date: Optional[date] = None,
    trip_shape: Optional[TrustedActionTripType] = None,
    traveler_count: Optional[int] = None,
    party: Optional[Party] = None,
    partner: PartnerName,
) -> str:
    if partner == "aviasales" and domain == "flight":
        origin_iata = _scheduled_airport_iata(origin)
        destination_iata = _scheduled_airport_iata(destination)
        if origin_iata is None or destination_iata is None or departure_date is None:
            # No route/date to embed -- the compact search-results path
            # (below) 404s without a date (browser-verified, TWM-230
            # Increment 2d); "/" degrades to either the bare homepage or
            # the pre-filled *form* (build_query_params' "params" query,
            # which does accept a dateless route) rather than a broken
            # results page.
            return "/"
        suffix = aviasales_link.aviasales_passenger_suffix(_occupancy(party, traveler_count))
        segment = aviasales_link.aviasales_route_segment(
            origin_iata, destination_iata, departure_date, return_date, trip_shape
        )
        return f"search/{segment}{suffix}"
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
    if partner == "ixigo" and domain == "flight":
        origin_iata = _scheduled_airport_iata(origin)
        destination_iata = _scheduled_airport_iata(destination)
        if origin_iata is not None and destination_iata is not None and departure_date is not None:
            return "search/result/flight"
        # ixigo's flight search-result page 404s without a route -- unlike
        # ixigo trains, there is no bare, always-safe search surface at
        # this path; "flights" (the landing page with its own search form)
        # is the honest fallback, browser-verified (TWM-230 Increment 2c).
        return "flights"
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


def _scheduled_airport_iata(place: Optional[str]) -> Optional[str]:
    """The IATA code for ``place`` when it resolves to a real, currently-
    scheduled airport -- never a non-scheduled airstrip's code, never a
    guess. Shared by every flight-search partner (Aviasales, ixigo)."""

    resolution = resolve_airport(place) if place else None
    return resolution.iata if _is_scheduled_airport(resolution) else None


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

    if partner == "aviasales" and domain == "flight":
        return _aviasales_query_params(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
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
    if partner == "ixigo" and domain == "flight":
        return _ixigo_flight_query_params(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            trip_shape=trip_shape,
            occupancy=occupancy,
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


def _ixigo_flight_query_params(
    *,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    trip_shape: Optional[TrustedActionTripType],
    occupancy: Optional[Party],
    settings: TrustedActionSettings,
) -> dict[str, str]:
    """ixigo's flight search-result query shape, browser-verified
    (TWM-230 Increment 2c): ``https://www.ixigo.com/search/result/flight
    ?from=...&to=...&date=DDMMYYYY&adults=...&children=...&infants=...
    &class=e`` (plus ``returnDate=DDMMYYYY`` for a round trip). Unlike
    ixigo trains, the query params -- not the path -- carry the whole
    prefill, so an unresolved route/date degrades to tracking params only
    on the ``flights`` landing-page fallback (see ``_target_path``) rather
    than a labelled generic param set nobody reads there.
    """

    origin_iata = _scheduled_airport_iata(origin)
    destination_iata = _scheduled_airport_iata(destination)
    if origin_iata is None or destination_iata is None or departure_date is None:
        return tracking_params("ixigo", settings)

    params: dict[str, str] = {
        "from": origin_iata,
        "to": destination_iata,
        "date": departure_date.strftime("%d%m%Y"),
        # Economy-class default (documented judgement call, not researched
        # further this session) -- same posture as Aviasales' trip_class.
        "class": "e",
    }
    if trip_shape == "round_trip" and return_date is not None:
        params["returnDate"] = return_date.strftime("%d%m%Y")

    adults, children, infants = occupancy if occupancy is not None else (1, 0, 0)
    params["adults"] = str(max(1, adults))
    params["children"] = str(children)
    params["infants"] = str(infants)

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
                and _scheduled_airport_iata(request.origin) is not None
                and _scheduled_airport_iata(request.destination) is not None
            )
            note = (
                "Route, date, and traveler count open on Aviasales when airport resolution succeeds."
                if has_prefill
                else "Aviasales opens as a search surface; enter the route and date there."
            )
            return ("prefilled_search" if has_prefill else "destination_search", "Search Aviasales", note)
        if partner == "ixigo" and request.domain == "train":
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
        if partner == "ixigo" and request.domain == "flight":
            has_prefill = (
                has_route
                and has_date
                and _scheduled_airport_iata(request.origin) is not None
                and _scheduled_airport_iata(request.destination) is not None
            )
            note = (
                "Route, date, and traveler count open on ixigo when airport resolution succeeds."
                if has_prefill
                else "ixigo opens as a search surface; enter the route and date there."
            )
            return ("prefilled_search" if has_prefill else "destination_search", "Search ixigo flights", note)
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
    occupancy: Optional[Party],
    settings: TrustedActionSettings,
) -> dict[str, str]:
    origin_iata = _scheduled_airport_iata(origin)
    destination_iata = _scheduled_airport_iata(destination)
    params = aviasales_link.aviasales_form_params(
        origin_iata=origin_iata,
        destination_iata=destination_iata,
        departure_date=departure_date,
        occupancy=occupancy,
    )
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
