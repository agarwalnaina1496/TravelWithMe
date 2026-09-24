"""Guide day-plan validation, notably trip_duration coercion (TWM-207),
and deterministic remove_place command (TWM-232)."""

import pytest

from twm.services.trip_commands.errors import InvalidTripCommandError
from twm.services.trip_commands.planner_commands import (
    _validate_day_plan,
    apply_remove_place,
)


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


# ── apply_remove_place ────────────────────────────────────────────────────────


def _plan_state(places: list[str], day_plan: list[dict]) -> dict:
    from unittest.mock import MagicMock
    return {
        "trip_id": "test-trip",
        "planner_state": {
            "places": list(places),
            "day_plan": [dict(d) for d in day_plan],
            "revision": 1,
        },
    }, MagicMock()


def test_remove_place_drops_place_from_places_and_day_plan() -> None:
    state, logger = _plan_state(
        ["Gwalior Fort", "Orchha"],
        [
            {"day_number": 1, "places": ["Gwalior Fort"], "pace": "relaxed"},
            {"day_number": 2, "places": ["Orchha"], "pace": "balanced"},
        ],
    )
    result = apply_remove_place(logger, state, "Gwalior Fort")

    assert "Gwalior Fort" not in state["planner_state"]["places"]
    assert state["planner_state"]["places"] == ["Orchha"]
    assert state["planner_state"]["day_plan"][0]["places"] == []
    assert state["planner_state"]["day_plan"][1]["places"] == ["Orchha"]
    assert state["planner_state"]["revision"] == 2
    assert "Gwalior Fort" in result["message"]


def test_remove_place_is_case_insensitive() -> None:
    state, logger = _plan_state(
        ["Gwalior Fort"],
        [{"day_number": 1, "places": ["Gwalior Fort"], "pace": "relaxed"}],
    )
    apply_remove_place(logger, state, "gwalior fort")
    assert state["planner_state"]["places"] == []


def test_remove_place_rejects_unknown_place() -> None:
    state, logger = _plan_state(
        ["Orchha"],
        [{"day_number": 1, "places": ["Orchha"], "pace": "relaxed"}],
    )
    with pytest.raises(InvalidTripCommandError, match="not in the current plan"):
        apply_remove_place(logger, state, "Khajuraho")


def test_remove_place_rejects_frozen_plan() -> None:
    state, logger = _plan_state(["Orchha"], [])
    state["planner_state"]["frozen_plan"] = {"guide_state": {}}
    with pytest.raises(InvalidTripCommandError, match="frozen"):
        apply_remove_place(logger, state, "Orchha")
