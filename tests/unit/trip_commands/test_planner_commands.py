"""Guide day-plan validation, notably trip_duration coercion (TWM-207)."""

import pytest

from twm.services.trip_commands.errors import InvalidTripCommandError
from twm.services.trip_commands.planner_commands import _validate_day_plan


def _state(trip_duration: object, day_plan: list[dict]) -> dict:
    places = [place for day in day_plan for place in day["places"]]
    return {
        "planner_state": {"day_plan": day_plan, "places": places},
        "trip_context": {"trip_duration": trip_duration},
    }


def _day_plan(n: int) -> list[dict]:
    return [{"day_number": i, "places": [f"Place {i}"]} for i in range(1, n + 1)]


def test_validate_day_plan_accepts_an_int_trip_duration() -> None:
    _validate_day_plan(_state(3, _day_plan(3)))


def test_validate_day_plan_coerces_a_numeral_string_trip_duration() -> None:
    # Previously: len(day_plan) != trip_duration is always True for a str,
    # since int != str in Python — a correct plan was permanently rejected.
    _validate_day_plan(_state("3", _day_plan(3)))


def test_validate_day_plan_coerces_a_whole_float_trip_duration() -> None:
    # Previously: range(1, trip_duration + 1) raised an uncaught TypeError
    # for a float ("float object cannot be interpreted as an integer").
    _validate_day_plan(_state(3.0, _day_plan(3)))


def test_validate_day_plan_rejects_a_non_numeral_string_trip_duration() -> None:
    with pytest.raises(InvalidTripCommandError, match="whole number of days"):
        _validate_day_plan(_state("a month", _day_plan(3)))


def test_validate_day_plan_rejects_a_fractional_float_trip_duration() -> None:
    with pytest.raises(InvalidTripCommandError, match="whole number of days"):
        _validate_day_plan(_state(3.5, _day_plan(3)))


def test_validate_day_plan_rejects_a_boolean_trip_duration() -> None:
    with pytest.raises(InvalidTripCommandError, match="whole number of days"):
        _validate_day_plan(_state(True, _day_plan(1)))
