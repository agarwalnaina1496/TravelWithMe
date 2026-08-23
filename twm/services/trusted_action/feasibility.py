"""Trip-feasibility calculator (TWM-131, rebuilt for TWM-195).

Populates ``twm.schemas.trusted_action.TripFeasibilityAssessment`` /
``ModeFeasibility``. TWM-131's original version depended on two things that
were never fully wired: a hardcoded ~20-city haversine table
(``_KNOWN_CITY_COORDINATES`` / ``StaticCityDistanceEstimator``) for flight/
drive, and an optional, never-supplied ``duration_estimator`` for train/bus
-- so train/bus were silently omitted from every real assessment, and any
route to a city outside the fixed table returned no assessment at all
(Dashboard Bookings then treated "no assessment" as "every mode is
feasible", which is the TWM-195 bug).

TWM-195 replaces all of that with a single internal LLM route-mode
classifier (``twm.services.trusted_action.route_classifier``) that judges
all four modes (flight/train/bus/drive) together for any route, known city
or not. There is no fallback to the old static-table/haversine path -- it
has been removed entirely, not kept as a secondary check. A classifier
failure, timeout, invalid output, or low-confidence result never falls back
to "feasible": every mode gets an honest ``status="unknown"`` instead.
"""

from typing import Optional

from ...schemas.atlas import AtlasReference
from ...schemas.trusted_action import ModeFeasibility, TransportMode, TripFeasibilityAssessment
from .route_classifier import RouteClassifier

_ALL_MODES: tuple[TransportMode, ...] = ("flight", "train", "bus", "drive")

_UNKNOWN_REASON = (
    "Could not confidently determine whether this mode is a genuine option "
    "for this route."
)
_PLAUSIBLE_REASON_TEMPLATE = (
    "Route-mode classifier judged {mode} a genuinely plausible way to "
    "travel this route."
)
_IMPLAUSIBLE_REASON_TEMPLATE = (
    "Route-mode classifier judged {mode} not a genuinely plausible way to "
    "travel this route."
)


async def assess_trip_feasibility(
    origin: str,
    destination: str,
    *,
    classifier: RouteClassifier,
) -> Optional[TripFeasibilityAssessment]:
    """Assemble a ``TripFeasibilityAssessment`` covering all four transport
    modes for a route.

    Returns ``None`` only for a structurally degenerate route (blank/
    identical origin and destination) — never as a stand-in for "the
    classifier could not judge this route" (that is ``status="unknown"``
    on every mode instead, so a caller can never mistake "nothing to show"
    for "everything is feasible").
    """

    origin = origin.strip()
    destination = destination.strip()
    if not origin or not destination or origin.casefold() == destination.casefold():
        return None

    plausibility = await classifier.classify(origin, destination)
    modes = [_build_mode(mode, plausibility) for mode in _ALL_MODES]
    return TripFeasibilityAssessment(modes=modes)


def _build_mode(
    mode: TransportMode, plausibility: Optional[dict[TransportMode, bool]]
) -> ModeFeasibility:
    if plausibility is None:
        return ModeFeasibility(
            mode=mode,
            status="unknown",
            duration_source="llm_estimated",
            reason=_UNKNOWN_REASON,
        )

    is_plausible = plausibility.get(mode, False)
    if is_plausible:
        return ModeFeasibility(
            mode=mode,
            status="feasible",
            duration_source="llm_estimated",
            reason=_PLAUSIBLE_REASON_TEMPLATE.format(mode=mode),
            verification=AtlasReference(status="GENERAL_GUIDANCE"),
        )
    return ModeFeasibility(
        mode=mode,
        status="ruled_out",
        duration_source="llm_estimated",
        reason=_IMPLAUSIBLE_REASON_TEMPLATE.format(mode=mode),
        verification=AtlasReference(status="GENERAL_GUIDANCE"),
    )
