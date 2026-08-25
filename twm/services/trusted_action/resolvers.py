"""SEARCH_REDIRECT partner URL/query-parameter assembly (TWM-131).

Pure string/query assembly only — no outbound HTTP call is ever made here,
and no credential is required to build a safe (non-affiliate-tracked) link.
``twm.schemas.trusted_action.ActionTarget`` is the only place a real
externally-reachable URL gets assembled (fixed base domain + validated path
+ validated query params); this module only decides *which* path and
*which* params a given partner/domain pair gets, then hands them to
``ActionTarget``.

Judgement call (documented, not fabricated): Hotellook/Booking.com/Agoda's
exact public deep-link path conventions are not confirmed this session
beyond the shared Travelpayouts ``marker=`` tracking convention already used
by ``twm/services/flight_search/aviasales.py``. Rather than guess an
undocumented partner-specific path/param naming scheme, every partner here
uses the same generic ``search`` path plus clearly-named generic query
parameters (``origin``, ``destination``, ``depart_date``, ``return_date``,
``travelers``). Wiring each partner's actual documented deep-link format is
a natural follow-up once each is individually researched/confirmed.

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

from datetime import date
from typing import Optional

from ...schemas.trusted_action import ActionTarget, PartnerName, TrustedActionDomain, TrustedActionRequest
from .settings import TrustedActionSettings

# Generic, domain-scoped search path segment per partner. Every partner
# resolves to the same "search" path today (see module docstring) — kept as
# a per-partner dict (not a bare constant) so a future story can give an
# individual partner a confirmed, more specific path without touching the
# others.
_SEARCH_PATH: dict[PartnerName, str] = {
    "aviasales": "search",
    "ixigo": "search",
    "redbus": "search",
    "hotellook": "search",
    "booking_com": "search",
    "agoda": "search",
    "hostelworld": "search",
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
        path=_SEARCH_PATH[partner],
        query_params=build_query_params(
            domain=request.domain,
            origin=request.origin,
            destination=request.destination,
            departure_date=request.departure_date,
            return_date=request.return_date,
            traveler_count=request.traveler_count,
            partner=partner,
            settings=settings,
        ),
    )


def build_query_params(
    *,
    domain: TrustedActionDomain,
    origin: Optional[str],
    destination: Optional[str],
    departure_date: Optional[date],
    return_date: Optional[date],
    traveler_count: Optional[int],
    partner: PartnerName,
    settings: TrustedActionSettings,
) -> dict[str, str]:
    """Pure value -> query-parameter mapping, deliberately independent of
    ``TrustedActionRequest`` (plain primitives in, plain dict out) so it can
    be unit-tested directly without instantiating a request schema model,
    per this repo's rule against schema-instantiating unit tests."""

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


_TRAVELPAYOUTS_PARTNERS: frozenset[PartnerName] = frozenset(
    {"aviasales", "hotellook", "booking_com", "agoda"}
)


def tracking_params(partner: PartnerName, settings: TrustedActionSettings) -> dict[str, str]:
    if partner == "ixigo" and settings.ixigo_affiliate_id:
        return {"affiliate_id": settings.ixigo_affiliate_id}
    if partner in _TRAVELPAYOUTS_PARTNERS and settings.travelpayouts_marker:
        return {"marker": settings.travelpayouts_marker}
    # redbus, hostelworld: no confirmed tracking parameter this session.
    return {}
