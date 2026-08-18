"""Trip-feasibility calculator (TWM-131).

Populates ``twm.schemas.trusted_action.TripFeasibilityAssessment`` /
``ModeFeasibility``. The deterministic parts — distance/duration threshold
logic, bounding/validation of any duration estimate, and assembling the
final ``TripFeasibilityAssessment`` — are fully built here. The two pieces
that would require large new infrastructure are deliberately NOT built in
this story and are instead expressed as small injected protocols with one
working example implementation:

- ``DistanceEstimator``: flight/drive need a real distance between two
  named places. No geocoding/routing API integration exists anywhere in
  this repository yet, and building one is out of scope for this story.
  ``StaticCityDistanceEstimator`` is a working example implementation
  covering a fixed list of ~20 Indian cities/regions that already appear in
  this codebase's fixtures (tests/resources/*, kb/spiti_valley.yaml) —
  documented as a bounded, known-cities-only capability. An unknown city
  yields ``None`` (an honest "cannot assess" outcome), never a fabricated
  number. Wiring a real geocoding/routing provider is a natural, separately
  scoped follow-up; this module's ``DistanceEstimator`` protocol is exactly
  what that follow-up would implement, with no caller-side change required.
- ``DurationEstimator``: train/bus durations require an LLM estimate
  (duration_source="llm_estimated", bounded, carrying a GENERAL_GUIDANCE
  AtlasReference per twm/schemas/trusted_action.py). Building a new bounded
  Guide/Atlas-style agent-engine call with correct prompt-versioning is a
  meaningfully sized effort on its own (see twm/services/agent_engine/ and
  twm/prompts/) and is deliberately NOT attempted here to avoid doing it
  incorrectly/partially. Instead, ``DurationEstimator`` is a plain injected
  protocol the caller supplies; ``assess_trip_feasibility`` performs the
  bounding/validation (via ``ModeFeasibility``'s own validators, which
  already enforce the 3-day/4320-minute cap and the never-VERIFIED honesty
  rule) around whatever the caller's estimator returns. When no
  ``duration_estimator`` is supplied, train/bus are simply omitted from the
  assessment (an honest "cannot assess" outcome, not a fabricated one) —
  wiring an actual LLM-backed estimator is a natural, separately scoped
  follow-up.
"""

import math
from typing import Optional, Protocol

from ...schemas.atlas import AtlasReference
from ...schemas.trusted_action import ModeFeasibility, TransportMode, TripFeasibilityAssessment

# A deterministic, single-day-drive-plausibility cutoff. Not researched
# against a specific routing provider (none exists in this repo yet) — a
# clearly named, documented policy constant, easy to retune once a real
# routing/duration source is wired.
_DRIVE_INFEASIBLE_ABOVE_KM = 800.0

_EARTH_RADIUS_KM = 6371.0

# Fixed, bounded lookup of major Indian cities/regions that already appear
# in this codebase's fixtures (tests/resources/*, kb/spiti_valley.yaml) —
# not a general-purpose geocoder. Coordinates are approximate city/region
# centroids, sufficient for a great-circle distance estimate, not for
# routing. Keys are matched case-insensitively; a name not in this table
# yields "cannot assess" (None), never a fabricated distance.
_KNOWN_CITY_COORDINATES: dict[str, tuple[float, float]] = {
    "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090),
    "agra": (27.1767, 78.0081),
    "jaipur": (26.9124, 75.7873),
    "mumbai": (19.0760, 72.8777),
    "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946),
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
}


class DistanceEstimator(Protocol):
    def estimate_km(self, origin: str, destination: str) -> Optional[float]:
        """Great-circle (or better) distance between ``origin`` and
        ``destination`` in kilometers, or None when it cannot be
        determined (unknown place, no routing data) — never a fabricated
        number."""
        ...


class DurationEstimator(Protocol):
    def estimate_minutes(
        self, origin: str, destination: str, mode: TransportMode
    ) -> Optional[tuple[int, AtlasReference]]:
        """An estimated overland duration in minutes for ``mode``
        ("train" or "bus" only) plus its bounded honesty tag, or None when
        no estimate is available. The returned AtlasReference must never
        claim VERIFIED (an LLM duration estimate is never fact-checked) —
        enforced again, defensively, by ModeFeasibility's own validator."""
        ...


class StaticCityDistanceEstimator:
    """Working example ``DistanceEstimator``: haversine distance over a
    fixed table of ~20 Indian cities/regions (see module docstring for the
    scope/honesty rationale). Not a geocoder — case-insensitive exact-name
    lookup only."""

    def estimate_km(self, origin: str, destination: str) -> Optional[float]:
        origin_coords = _KNOWN_CITY_COORDINATES.get(origin.strip().casefold())
        destination_coords = _KNOWN_CITY_COORDINATES.get(destination.strip().casefold())
        if origin_coords is None or destination_coords is None:
            return None
        return _haversine_km(origin_coords, destination_coords)


def _haversine_km(origin: tuple[float, float], destination: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, origin)
    lat2, lon2 = map(math.radians, destination)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    c = 2 * math.asin(min(1.0, math.sqrt(a)))
    return _EARTH_RADIUS_KM * c


def assess_trip_feasibility(
    origin: str,
    destination: str,
    *,
    distance_estimator: DistanceEstimator,
    duration_estimator: Optional[DurationEstimator] = None,
) -> Optional[TripFeasibilityAssessment]:
    """Assemble a ``TripFeasibilityAssessment`` for a route.

    Only includes the modes that could actually be judged: flight/drive are
    included when ``distance_estimator`` can resolve a distance for this
    origin/destination pair; train/bus are included only when
    ``duration_estimator`` is supplied and returns an estimate. Returns None
    (an honest "cannot assess" outcome) when nothing could be judged at
    all — never a fabricated assessment.
    """

    modes: list[ModeFeasibility] = []

    distance_km = distance_estimator.estimate_km(origin, destination)
    if distance_km is not None:
        modes.append(_flight_mode(distance_km))
        modes.append(_drive_mode(distance_km))

    if duration_estimator is not None:
        for mode in ("train", "bus"):
            estimate = duration_estimator.estimate_minutes(origin, destination, mode)  # type: ignore[arg-type]
            if estimate is not None:
                minutes, reference = estimate
                modes.append(_overland_mode(mode, minutes, reference))  # type: ignore[arg-type]

    if not modes:
        return None
    return TripFeasibilityAssessment(modes=modes)


def _flight_mode(distance_km: float) -> ModeFeasibility:
    # A commercial flight route is always treated as feasible once a
    # distance is computable — this calculator has no seat/route-existence
    # data, only distance, so it never rules a flight out.
    return ModeFeasibility(
        mode="flight",
        status="feasible",
        duration_source="computed",
        estimated_distance_km=round(distance_km, 1),
        reason=f"Computed great-circle distance of approximately {distance_km:.0f} km.",
    )


def _drive_mode(distance_km: float) -> ModeFeasibility:
    if distance_km > _DRIVE_INFEASIBLE_ABOVE_KM:
        return ModeFeasibility(
            mode="drive",
            status="ruled_out",
            duration_source="computed",
            estimated_distance_km=round(distance_km, 1),
            reason=(
                f"Computed distance of approximately {distance_km:.0f} km exceeds the "
                f"{_DRIVE_INFEASIBLE_ABOVE_KM:.0f} km single-trip drive threshold."
            ),
        )
    return ModeFeasibility(
        mode="drive",
        status="feasible",
        duration_source="computed",
        estimated_distance_km=round(distance_km, 1),
        reason=f"Computed distance of approximately {distance_km:.0f} km is within driving range.",
    )


def _overland_mode(mode: TransportMode, minutes: int, reference: AtlasReference) -> ModeFeasibility:
    return ModeFeasibility(
        mode=mode,
        status="feasible",
        duration_source="llm_estimated",
        estimated_duration_minutes=minutes,
        reason=f"LLM-estimated {mode} duration of approximately {minutes} minutes.",
        verification=reference,
    )
