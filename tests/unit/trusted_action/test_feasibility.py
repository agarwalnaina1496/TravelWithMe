"""Pure-function coverage for the trip-feasibility calculator. Takes no
TrustedActionRequest input anywhere — origin/destination are plain strings
and estimators are plain injected callables/protocols, not schema models.
"""

from twm.schemas.atlas import AtlasReference
from twm.services.trusted_action.feasibility import (
    StaticCityDistanceEstimator,
    assess_trip_feasibility,
)


class _NoOpDistanceEstimator:
    def estimate_km(self, origin, destination):
        return None


class _FixedDistanceEstimator:
    def __init__(self, km):
        self._km = km

    def estimate_km(self, origin, destination):
        return self._km


class _FixedDurationEstimator:
    def __init__(self, minutes):
        self._minutes = minutes

    def estimate_minutes(self, origin, destination, mode):
        if self._minutes is None:
            return None
        return self._minutes, AtlasReference(status="GENERAL_GUIDANCE")


def test_static_city_estimator_computes_a_plausible_distance_for_known_cities():
    estimator = StaticCityDistanceEstimator()
    km = estimator.estimate_km("Delhi", "Agra")
    assert km is not None
    # Delhi-Agra is ~200km road distance; great-circle should be in a
    # plausible neighborhood, not exact.
    assert 150 < km < 260


def test_static_city_estimator_is_case_insensitive_and_symmetric():
    estimator = StaticCityDistanceEstimator()
    forward = estimator.estimate_km("delhi", "AGRA")
    backward = estimator.estimate_km("Agra", "Delhi")
    assert forward == backward


def test_static_city_estimator_returns_none_for_an_unknown_city():
    estimator = StaticCityDistanceEstimator()
    assert estimator.estimate_km("Delhi", "Atlantis") is None


def test_assessment_is_none_when_nothing_can_be_judged():
    result = assess_trip_feasibility(
        "Atlantis", "Narnia", distance_estimator=_NoOpDistanceEstimator()
    )
    assert result is None


def test_short_distance_makes_drive_feasible_and_flight_feasible():
    result = assess_trip_feasibility(
        "Delhi", "Agra", distance_estimator=_FixedDistanceEstimator(200.0)
    )
    assert result is not None
    by_mode = {mode.mode: mode for mode in result.modes}
    assert by_mode["flight"].status == "feasible"
    assert by_mode["flight"].duration_source == "computed"
    assert by_mode["flight"].verification is None
    assert by_mode["drive"].status == "feasible"
    assert by_mode["drive"].duration_source == "computed"
    assert "train" not in by_mode
    assert "bus" not in by_mode


def test_long_distance_rules_out_drive_but_not_flight():
    result = assess_trip_feasibility(
        "Delhi", "Chennai", distance_estimator=_FixedDistanceEstimator(2200.0)
    )
    assert result is not None
    by_mode = {mode.mode: mode for mode in result.modes}
    assert by_mode["flight"].status == "feasible"
    assert by_mode["drive"].status == "ruled_out"


def test_duration_estimator_adds_bounded_llm_estimated_train_and_bus_modes():
    result = assess_trip_feasibility(
        "Delhi",
        "Agra",
        distance_estimator=_FixedDistanceEstimator(200.0),
        duration_estimator=_FixedDurationEstimator(180),
    )
    assert result is not None
    by_mode = {mode.mode: mode for mode in result.modes}
    for mode_name in ("train", "bus"):
        mode = by_mode[mode_name]
        assert mode.duration_source == "llm_estimated"
        assert mode.estimated_duration_minutes == 180
        assert mode.verification is not None
        assert mode.verification.status == "GENERAL_GUIDANCE"


def test_duration_estimator_returning_none_omits_train_and_bus():
    result = assess_trip_feasibility(
        "Delhi",
        "Agra",
        distance_estimator=_FixedDistanceEstimator(200.0),
        duration_estimator=_FixedDurationEstimator(None),
    )
    assert result is not None
    by_mode = {mode.mode: mode for mode in result.modes}
    assert "train" not in by_mode
    assert "bus" not in by_mode
