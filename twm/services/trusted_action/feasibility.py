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

- Flight is excluded below ``_FLIGHT_INFEASIBLE_BELOW_KM`` -- a short local
  hop that no domestic carrier operates as a scheduled route (calibrated
  against the story's own examples: Bhubaneswar->Puri ~60km and
  Puri->Konark ~35km must exclude flight; Bangalore->Mangalore ~352km must
  include it).
- Drive is excluded above ``_DRIVE_INFEASIBLE_ABOVE_KM`` -- a distance a
  traveler would not reasonably do as a single road trip leg (reused from
  the pre-TWM-195 static-table implementation; still comfortably above
  Bangalore->Mangalore's ~352km).
- Train and bus are always included when the pair's distance is known --
  there is no deterministic negative signal for either without real
  rail/road network data. This is a known, documented limitation, not an
  oversight; a follow-up story should add real train/bus network data.

When either city cannot be resolved, the pair's distance is unknown and this
returns a completely empty ``modes: []`` -- fail closed, never "assume every
mode is feasible" (the original TWM-195 bug) and never partially feasible.
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

_FLIGHT_INCLUDED_REASON = (
    "Distance between these cities is long enough that a domestic flight "
    "is a genuine way to travel this route."
)
_DRIVE_INCLUDED_REASON = "This distance is a reasonable single-trip drive."
_TRAIN_REASON = (
    "Train is included by default for routes with a known distance -- no "
    "route-specific rail-network exclusion is applied in this first slice."
)
_BUS_REASON = (
    "Bus is included by default for routes with a known distance -- no "
    "route-specific road-network exclusion is applied in this first slice."
)


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
    mode: TransportMode, *, distance_km: float, reason: str
) -> ModeFeasibility:
    return ModeFeasibility(
        mode=mode,
        status="feasible",
        duration_source="computed",
        estimated_distance_km=round(distance_km, 1),
        reason=reason,
        verification=AtlasReference(status="GENERAL_GUIDANCE"),
    )


def assess_trip_feasibility(origin: str, destination: str) -> TripFeasibilityAssessment:
    """Assemble a ``TripFeasibilityAssessment`` for a route using bounded,
    deterministic distance rules. Always returns a real assessment --
    never ``None`` -- with ``modes: []`` for a degenerate route (blank/
    identical origin and destination) or an unknown city pair (either city
    missing from the bounded table). Callers must not treat a missing/None
    return as a possibility; there is none.
    """

    origin = origin.strip()
    destination = destination.strip()
    if not origin or not destination or origin.casefold() == destination.casefold():
        return TripFeasibilityAssessment(modes=[])

    distance_km = _resolve_known_distance_km(origin, destination)
    if distance_km is None:
        return TripFeasibilityAssessment(modes=[])

    modes: list[ModeFeasibility] = []
    if distance_km >= _FLIGHT_INFEASIBLE_BELOW_KM:
        modes.append(_mode("flight", distance_km=distance_km, reason=_FLIGHT_INCLUDED_REASON))
    if distance_km <= _DRIVE_INFEASIBLE_ABOVE_KM:
        modes.append(_mode("drive", distance_km=distance_km, reason=_DRIVE_INCLUDED_REASON))
    modes.append(_mode("train", distance_km=distance_km, reason=_TRAIN_REASON))
    modes.append(_mode("bus", distance_km=distance_km, reason=_BUS_REASON))

    return TripFeasibilityAssessment(modes=modes)
