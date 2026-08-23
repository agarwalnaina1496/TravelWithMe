"""Pure-function coverage for the trip-feasibility calculator (TWM-195).

Takes no TrustedActionRequest input anywhere — origin/destination are plain
strings and the classifier is a plain injected ``RouteClassifier`` protocol
implementation, not a schema model.
"""

import asyncio

import pytest

from twm.services.trusted_action.feasibility import assess_trip_feasibility


class _FixedClassifier:
    """Returns a fixed plausibility mapping (or None) regardless of route."""

    def __init__(self, plausibility):
        self._plausibility = plausibility

    async def classify(self, origin, destination):
        return self._plausibility


class _RecordingClassifier:
    """Records how many times / with what args it was called, so a test
    can prove all four modes are judged together in one call."""

    def __init__(self, plausibility):
        self._plausibility = plausibility
        self.calls = []

    async def classify(self, origin, destination):
        self.calls.append((origin, destination))
        return self._plausibility


def _run(coro):
    return asyncio.run(coro)


def test_removed_static_city_table_and_haversine_symbols_do_not_exist():
    """Regression guard (TWM-195): the old ~20-city haversine path must be
    gone entirely, not kept as a fallback."""
    import twm.services.trusted_action.feasibility as feasibility_module

    for removed_symbol in (
        "_KNOWN_CITY_COORDINATES",
        "StaticCityDistanceEstimator",
        "DistanceEstimator",
        "DurationEstimator",
        "_DRIVE_INFEASIBLE_ABOVE_KM",
        "_haversine_km",
    ):
        assert not hasattr(feasibility_module, removed_symbol), removed_symbol


def test_degenerate_route_returns_none():
    assert _run(
        assess_trip_feasibility("Paris", "Paris", classifier=_FixedClassifier(None))
    ) is None
    assert _run(
        assess_trip_feasibility("", "Somewhere", classifier=_FixedClassifier(None))
    ) is None


def test_classifier_is_called_once_for_all_four_modes_together():
    classifier = _RecordingClassifier(
        {"flight": True, "train": False, "bus": False, "drive": True}
    )
    result = _run(
        assess_trip_feasibility("Bhubaneswar", "Puri", classifier=classifier)
    )
    assert result is not None
    assert len(classifier.calls) == 1
    assert classifier.calls[0] == ("Bhubaneswar", "Puri")
    assert {mode.mode for mode in result.modes} == {"flight", "train", "bus", "drive"}


def test_local_route_rules_out_route_absurd_modes_like_flight():
    # Bhubaneswar -> Puri: a local/short-hop route where flight is
    # route-absurd (Linear TWM-195 acceptance criterion).
    result = _run(
        assess_trip_feasibility(
            "Bhubaneswar",
            "Puri",
            classifier=_FixedClassifier(
                {"flight": False, "train": True, "bus": True, "drive": True}
            ),
        )
    )
    by_mode = {mode.mode: mode for mode in result.modes}
    assert by_mode["flight"].status == "ruled_out"
    assert by_mode["train"].status == "feasible"
    assert by_mode["bus"].status == "feasible"
    assert by_mode["drive"].status == "feasible"


def test_multi_valid_mode_route_keeps_all_route_valid_modes():
    # Bangalore -> Mangalore: multiple modes can legitimately all be valid;
    # Backend must not prune to one "best" mode (that is a UI concern).
    result = _run(
        assess_trip_feasibility(
            "Bangalore",
            "Mangalore",
            classifier=_FixedClassifier(
                {"flight": True, "train": True, "bus": True, "drive": False}
            ),
        )
    )
    by_mode = {mode.mode: mode for mode in result.modes}
    assert by_mode["flight"].status == "feasible"
    assert by_mode["train"].status == "feasible"
    assert by_mode["bus"].status == "feasible"
    assert by_mode["drive"].status == "ruled_out"


def test_unknown_classification_produces_unknown_for_every_mode_never_feasible():
    result = _run(
        assess_trip_feasibility(
            "Some Remote Village", "Another Remote Village", classifier=_FixedClassifier(None)
        )
    )
    assert result is not None
    for mode in result.modes:
        assert mode.status == "unknown"
        assert mode.verification is None


def test_unknown_route_previously_in_and_out_of_the_static_table_are_treated_consistently():
    # Delhi (previously in the removed static table) and a village that was
    # never in it must behave identically now — both routed purely through
    # the classifier, with no special-cased table lookup for either.
    known_city_result = _run(
        assess_trip_feasibility("Delhi", "Somewhere Unmapped", classifier=_FixedClassifier(None))
    )
    unknown_city_result = _run(
        assess_trip_feasibility(
            "Nowhere Mapped", "Somewhere Unmapped", classifier=_FixedClassifier(None)
        )
    )
    assert {mode.status for mode in known_city_result.modes} == {"unknown"}
    assert {mode.status for mode in unknown_city_result.modes} == {"unknown"}


def test_feasible_and_ruled_out_modes_carry_general_guidance_verification():
    result = _run(
        assess_trip_feasibility(
            "Delhi",
            "Agra",
            classifier=_FixedClassifier(
                {"flight": True, "train": True, "bus": True, "drive": True}
            ),
        )
    )
    for mode in result.modes:
        assert mode.status == "feasible"
        assert mode.verification is not None
        assert mode.verification.status == "GENERAL_GUIDANCE"
