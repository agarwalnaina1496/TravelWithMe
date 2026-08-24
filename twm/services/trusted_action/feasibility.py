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

Signal used: a small, explicitly bounded city-to-city great-circle distance
table (``_KNOWN_CITY_COORDINATES``), covering only the ~20-25 Indian
cities/regions that already appear in this codebase's fixtures
(``tests/resources/*``, ``kb/spiti_valley.yaml``) and the equivalent
``CITY_IATA`` list in TWM-UI's ``app/src/lib/bookingCatalog.js`` (city-name
conventions kept consistent with that list on purpose, so a name that
resolves on one side resolves the same way on the other). This is
explicitly NOT meant to grow into a general-purpose route-plausibility
source of truth -- the Linear issue is explicit that expanding this table
is not "the fix". It is one bounded, conservative signal for this first
deterministic slice; a real route-mode provider/classifier (actual airline
route networks, rail/road network data) is an explicit future follow-up
story once this slice proves insufficient.

Rules (only apply when BOTH origin and destination resolve in the bounded
table -- see ``assess_trip_feasibility`` for the unknown-pair behavior):

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

When either city is missing from the bounded table, the pair's distance is
unknown and this returns a completely empty ``modes: []`` -- fail closed,
never "assume every mode is feasible" (the original TWM-195 bug) and never
partially feasible.
"""

import math
from typing import Optional

from ...schemas.atlas import AtlasReference
from ...schemas.trusted_action import ModeFeasibility, TransportMode, TripFeasibilityAssessment

_EARTH_RADIUS_KM = 6371.0

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

# Fixed, bounded lookup of major Indian cities/regions already used
# elsewhere in this codebase's fixtures (tests/resources/*,
# kb/spiti_valley.yaml) and mirrored in TWM-UI's bookingCatalog.js
# CITY_IATA list, plus the three route-pairs the Linear issue calibrates
# against (Bhubaneswar/Puri/Konark) and Mangalore (Bangalore->Mangalore's
# other endpoint). Coordinates are approximate city/region centroids,
# sufficient for a great-circle distance estimate, never for routing.
# Case-insensitive exact-name lookup only, not a geocoder. Deliberately NOT
# expanded beyond this bounded set -- see module docstring.
_KNOWN_CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090),
    "agra": (27.1767, 78.0081),
    "jaipur": (26.9124, 75.7873),
    "mumbai": (19.0760, 72.8777),
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
    "mangalore": (12.9141, 74.8560),
    "mangaluru": (12.9141, 74.8560),
    "kochi": (9.9312, 76.2673),
    "cochin": (9.9312, 76.2673),
    "alleppey": (9.4981, 76.3388),
    "alappuzha": (9.4981, 76.3388),
    "coorg": (12.3375, 75.8069),
    "madikeri": (12.4244, 75.7382),
    "goa": (15.2993, 74.1240),
    "panaji": (15.4909, 73.8278),
    "rishikesh": (30.0869, 78.2676),
    "manali": (32.2432, 77.1892),
    "spiti valley": (32.2461, 78.0349),
    "spiti": (32.2461, 78.0349),
    "jaisalmer": (26.9157, 70.9083),
    "udaipur": (24.5854, 73.7125),
    "varanasi": (25.3176, 82.9739),
    "chennai": (13.0827, 80.2707),
    "kolkata": (22.5726, 88.3639),
    "hyderabad": (17.3850, 78.4867),
    "pune": (18.5204, 73.8567),
    "amritsar": (31.6340, 74.8723),
    "bhubaneswar": (20.2961, 85.8245),
    "puri": (19.8135, 85.8312),
    "konark": (19.8876, 86.0945),
}

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
    origin_coords = _KNOWN_CITY_COORDINATES.get(origin.strip().casefold())
    destination_coords = _KNOWN_CITY_COORDINATES.get(destination.strip().casefold())
    if origin_coords is None or destination_coords is None:
        return None
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
