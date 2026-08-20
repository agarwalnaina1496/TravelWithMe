"""Trip-context merge semantics shared across Scout, Meridian, and Guide."""

import pytest

from twm.services.trip_commands.errors import InvalidTripCommandError
from twm.services.trip_commands.state import (
    STAGE_TRANSITIONS,
    VALID_STAGES,
    merge_trip_context,
    set_stage,
)


def test_merge_trip_context_unions_preferences_case_insensitively() -> None:
    target = {"preferences": ["pilgrimage", "relaxed"]}

    merge_trip_context(target, {"preferences": ["PILGRIMAGE", "quiet"]})

    assert target["preferences"] == ["pilgrimage", "relaxed", "quiet"]


def test_merge_trip_context_unions_exclusions_and_coerces_non_list_prior_value() -> None:
    target = {"exclusions": "not-a-list-yet"}

    merge_trip_context(target, {"exclusions": ["river rafting"]})

    assert target["exclusions"] == ["river rafting"]


def test_merge_trip_context_overwrites_non_union_fields() -> None:
    target = {"destinations": ["Delhi"], "trip_duration": 3}

    merge_trip_context(target, {"destinations": ["Agra", "Jaipur"], "trip_duration": 5})

    assert target["destinations"] == ["Agra", "Jaipur"]
    assert target["trip_duration"] == 5


def test_merge_trip_context_recurses_into_nested_dicts() -> None:
    target = {"selected_option": {"id": "a", "name": "A"}}

    merge_trip_context(target, {"selected_option": {"name": "B"}})

    assert target["selected_option"] == {"id": "a", "name": "B"}


def test_set_stage_writes_a_valid_stage() -> None:
    state = {"stage": "new"}

    set_stage(state, "matching")

    assert state["stage"] == "matching"


@pytest.mark.parametrize("stage", sorted(VALID_STAGES))
def test_set_stage_accepts_every_canonical_stage(stage: str) -> None:
    state = {"stage": "new"}

    set_stage(state, stage)

    assert state["stage"] == stage


def test_set_stage_rejects_an_out_of_enum_value() -> None:
    state = {"stage": "matching"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, "mtching")

    # Rejected write must not have mutated state.
    assert state["stage"] == "matching"


def test_set_stage_rejects_empty_string() -> None:
    state = {"stage": "matching"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, "")


# STAGE_TRANSITIONS is documented, not yet enforced (TWM-188) — these
# guards only keep the table structurally consistent with VALID_STAGES so
# it stays trustworthy as a reference until enforcement is wired in.


def test_stage_transitions_has_an_entry_for_every_canonical_stage() -> None:
    assert set(STAGE_TRANSITIONS.keys()) == VALID_STAGES


def test_stage_transitions_targets_are_all_canonical_stages() -> None:
    for from_stage, targets in STAGE_TRANSITIONS.items():
        assert targets <= VALID_STAGES, f"{from_stage} has an out-of-enum target: {targets - VALID_STAGES}"


def test_stage_transitions_planned_is_terminal() -> None:
    assert STAGE_TRANSITIONS["planned"] == frozenset()
