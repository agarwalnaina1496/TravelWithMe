"""Trip-feasibility calculator (TWM-131, rebuilt again for TWM-195 root fix).

Populates ``twm.schemas.trusted_action.TripFeasibilityAssessment`` /
``ModeFeasibility``. Two prior rounds of this story tried an internal LLM
route-mode classifier (LangGraph-backed, ``route_classifier.py`` /
``LLMRouteClassifier``) to judge plausibility for all four transport modes.
The product owner explicitly rejected that approach on re-review: no new
internal LLM/agent/classifier runtime, no n8n or LangGraph route-mode
classification, for this first slice (see Linear TWM-195, "Confirmed
Implementation Direction"). This module is a full rewrite back to pure,
synchronous, deterministic backend-code rules -- no network call, no agent
engine, no LLM of any kind.

Signal used: the bundled OurAirports-backed resolver already used by flight
search (``twm.services.airport_resolution.resolve_airport``). This keeps
city-name aliases and fallback handling in one backend-owned place instead
of maintaining a second hardcoded coordinate table in this module.

Rules (only apply when BOTH origin and destination resolve -- see
``assess_trip_feasibility`` for the unknown-pair behavior):

- Flight is marked not feasible below ``_FLIGHT_INFEASIBLE_BELOW_KM`` -- a
  short local hop where the deterministic distance rule should not present
  air as a useful option (calibrated against the story's own examples:
  Bhubaneswar->Puri ~60km and Puri->Konark ~35km must exclude flight;
  Bangalore->Mangalore ~352km must include it).
- Drive is excluded above ``_DRIVE_INFEASIBLE_ABOVE_KM`` -- a distance a
  traveler would not reasonably do as a single road trip leg (reused from
  the pre-TWM-195 static-table implementation; still comfortably above
  Bangalore->Mangalore's ~352km).
- Train is included when the pair's distance is known. Bus is marked not
  feasible above ``_BUS_INFEASIBLE_ABOVE_KM``.

When either city cannot be resolved, the pair's distance is unknown and this
returns every mode as ``not_feasible`` with a cannot-assess reason -- fail
closed, never "assume every mode is feasible" (the original TWM-195 bug).

TWM-215: a gateway hub for a hubless endpoint may itself be a rail-only town
the OurAirports resolver cannot place. When the caller supplies Atlas's
``long_haul_distance_km`` ballpark for such a pair, this uses that distance
instead of returning empty -- but excludes flight unconditionally (a hub with
no resolvable airport has no scheduled air service to assess), yielding
train/bus (and drive when within range).
"""

import logging
import math
from typing import Optional

from ...schemas.atlas import AtlasReference
from ...schemas.trusted_action import ModeFeasibility, TransportMode, TripFeasibilityAssessment
from ..airport_resolution import resolve_airport

_EARTH_RADIUS_KM = 6371.0
logger = logging.getLogger(__name__)

# A short-local-hop cutoff: below this distance, no Indian domestic carrier
# operates a scheduled route in practice (there is no commercial case for a
# same-city-cluster flight). Calibrated strictly between the story's own
# exclude/include examples -- Bhubaneswar->Puri (~60km) and Puri->Konark
# (~35km) must exclude flight; Bangalore->Mangalore (~352km) must include
# it -- so any value in roughly (60, 352) works; 150km is chosen as a round,
# conservative middle value comfortably clear of both edges, matching this
# repo's convention of documented-judgment-call constants (see
# _DRIVE_INFEASIBLE_ABOVE_KM below).
_FLIGHT_INFEASIBLE_BELOW_KM = 150.0

# A single-day-drive-plausibility cutoff, reused from the pre-TWM-195
# static-table implementation (git history: twm/services/trusted_action/
# feasibility.py before commit a92634a). Still comfortably above
# Bangalore->Mangalore (~352km), which must remain drive-feasible.
_DRIVE_INFEASIBLE_ABOVE_KM = 800.0
_BUS_INFEASIBLE_ABOVE_KM = 1000.0

_FLIGHT_INCLUDED_REASON = "Distance supports flight as a practical long-haul option (~{distance_km:,.0f} km)."
_FLIGHT_SHORT_REASON = "Too short for flight under TWM's distance rule (~{distance_km:,.0f} km)."
_DRIVE_INCLUDED_REASON = "Within TWM's single-trip drive distance rule (~{distance_km:,.0f} km)."
_DRIVE_LONG_REASON = "Too far for a single-trip drive under TWM's distance rule (~{distance_km:,.0f} km)."
_TRAIN_REASON = (
    "Train remains feasible under TWM's deterministic distance rules (~{distance_km:,.0f} km); "
    "route frequency is not checked here."
)
_TRAIN_FALLBACK_REASON = (
    "Train remains feasible under TWM's approximate long-haul distance rules (~{distance_km:,.0f} km); "
    "route frequency is not checked here."
)
_BUS_REASON = (
    "Bus remains feasible under TWM's deterministic distance rules (~{distance_km:,.0f} km); "
    "operator service is not checked here."
)
_BUS_FALLBACK_REASON = (
    "Bus remains feasible under TWM's approximate long-haul distance rules (~{distance_km:,.0f} km); "
    "operator service is not checked here."
)
_BUS_LONG_REASON = "Too far for bus under TWM's distance rule (~{distance_km:,.0f} km)."
_ROUTE_UNASSESSABLE_REASON = "This mode cannot be assessed because this route has no resolved distance."


def _haversine_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, origin)
    lat2, lon2 = map(math.radians, destination)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return _EARTH_RADIUS_KM * c


def _resolve_known_distance_km(origin: str, destination: str) -> Optional[float]:
    origin_match = resolve_airport(origin)
    destination_match = resolve_airport(destination)
    if origin_match is None or destination_match is None:
        if origin_match is None:
            logger.warning("Could not resolve origin city for feasibility: %s", origin)
        if destination_match is None:
            logger.warning("Could not resolve destination city for feasibility: %s", destination)
        return None
    origin_coords = (origin_match.lat, origin_match.lon)
    destination_coords = (destination_match.lat, destination_match.lon)
    return _haversine_km(origin_coords, destination_coords)


def _mode(
    mode: TransportMode, *, status: str, distance_km: float | None, reason: str
) -> ModeFeasibility:
    return ModeFeasibility(
        mode=mode,
        status=status,
        duration_source="computed",
        estimated_distance_km=round(distance_km, 1) if distance_km is not None else None,
        reason=reason,
        verification=AtlasReference(status="GENERAL_GUIDANCE"),
    )


_DISTANCE_FALLBACK_FLIGHT_EXCLUDED_REASON = (
    "Flight cannot be assessed because this gateway has no resolved airport; "
    "the rest uses an approximate long-haul distance (~{distance_km:,.0f} km)."
)


def _unassessable_modes() -> list[ModeFeasibility]:
    return [
        _mode(mode, status="not_feasible", distance_km=None, reason=_ROUTE_UNASSESSABLE_REASON)
        for mode in ("flight", "train", "bus", "drive")
    ]


def assess_trip_feasibility(
    origin: str,
    destination: str,
    long_haul_distance_km: Optional[float] = None,
) -> TripFeasibilityAssessment:
    """Assemble a ``TripFeasibilityAssessment`` for a route using bounded,
    deterministic distance rules. Always returns a real assessment --
    never ``None`` -- with one entry for each mode. Blank or identical routes,
    or an unknown city pair with no ``long_haul_distance_km`` fallback, fail
    closed with every mode marked ``not_feasible``.

    ``long_haul_distance_km`` (TWM-215): Atlas's ballpark for a gateway-hub
    pair. Used only when the resolver cannot place one/both cities; in that
    case flight is excluded unconditionally (no resolvable airport) and the
    remaining distance rules apply to the supplied distance.
    """

    origin = origin.strip()
    destination = destination.strip()
    if not origin or not destination or origin.casefold() == destination.casefold():
        return TripFeasibilityAssessment(modes=_unassessable_modes())

    distance_km = _resolve_known_distance_km(origin, destination)
    flight_assessable = distance_km is not None
    if distance_km is None:
        if long_haul_distance_km is None or long_haul_distance_km <= 0:
            return TripFeasibilityAssessment(modes=_unassessable_modes())
        distance_km = float(long_haul_distance_km)
    assert distance_km is not None

    modes: list[ModeFeasibility] = []
    if flight_assessable and distance_km >= _FLIGHT_INFEASIBLE_BELOW_KM:
        modes.append(_mode("flight", status="feasible", distance_km=distance_km,
                           reason=_FLIGHT_INCLUDED_REASON.format(distance_km=distance_km)))
    elif flight_assessable:
        modes.append(_mode("flight", status="not_feasible", distance_km=distance_km,
                           reason=_FLIGHT_SHORT_REASON.format(distance_km=distance_km)))
    else:
        modes.append(_mode("flight", status="not_feasible", distance_km=distance_km,
                           reason=_DISTANCE_FALLBACK_FLIGHT_EXCLUDED_REASON.format(distance_km=distance_km)))

    if distance_km <= _DRIVE_INFEASIBLE_ABOVE_KM:
        modes.append(_mode("drive", status="feasible", distance_km=distance_km,
                           reason=_DRIVE_INCLUDED_REASON.format(distance_km=distance_km)))
    else:
        modes.append(_mode("drive", status="not_feasible", distance_km=distance_km,
                           reason=_DRIVE_LONG_REASON.format(distance_km=distance_km)))

    train_reason = _TRAIN_REASON if flight_assessable else _TRAIN_FALLBACK_REASON
    bus_reason = _BUS_REASON if flight_assessable else _BUS_FALLBACK_REASON
    modes.append(_mode("train", status="feasible", distance_km=distance_km,
                       reason=train_reason.format(distance_km=distance_km)))
    if distance_km <= _BUS_INFEASIBLE_ABOVE_KM:
        modes.append(_mode("bus", status="feasible", distance_km=distance_km,
                           reason=bus_reason.format(distance_km=distance_km)))
    else:
        modes.append(_mode("bus", status="not_feasible", distance_km=distance_km,
                           reason=_BUS_LONG_REASON.format(distance_km=distance_km)))

    return TripFeasibilityAssessment(modes=modes)
