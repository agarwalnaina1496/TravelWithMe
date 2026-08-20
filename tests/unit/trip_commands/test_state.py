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
    # Starts from an unenforced from-stage (no "stage" key at all) so this
    # only exercises value-in-enum validation, not the transition graph —
    # "new" now enforces its own edges, see the tests below.
    state: dict = {}

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


# "new" is the first stage with enforced transitions (TWM-188).


@pytest.mark.parametrize("target", sorted(STAGE_TRANSITIONS["new"]))
def test_set_stage_from_new_accepts_its_documented_edges(target: str) -> None:
    state = {"stage": "new"}

    set_stage(state, target)

    assert state["stage"] == target


@pytest.mark.parametrize("target", sorted(VALID_STAGES - STAGE_TRANSITIONS["new"]))
def test_set_stage_from_new_rejects_every_other_target(target: str) -> None:
    state = {"stage": "new"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "new"


def test_set_stage_from_new_logs_a_warning_on_rejection() -> None:
    state = {"stage": "new", "trip_id": "trip-123"}
    logged: list[dict] = []

    class _StubLogger:
        def warning(self, message, **fields):
            logged.append({"message": message, **fields})

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, "planned", _StubLogger(), context="start_planning")

    assert len(logged) == 1
    assert logged[0]["event"] == "be.trip.stage.transition_rejected"
    assert logged[0]["trip_id"] == "trip-123"
    assert logged[0]["from_stage"] == "new"
    assert logged[0]["to_stage"] == "planned"
    assert logged[0]["context"] == "start_planning"


def test_set_stage_from_new_does_not_log_when_no_logger_given() -> None:
    state = {"stage": "new"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, "planned")


# "matching" is the second stage with enforced transitions (TWM-188).


@pytest.mark.parametrize("target", sorted(STAGE_TRANSITIONS["matching"]))
def test_set_stage_from_matching_accepts_its_documented_edges(target: str) -> None:
    state = {"stage": "matching"}

    set_stage(state, target)

    assert state["stage"] == target


@pytest.mark.parametrize("target", sorted(VALID_STAGES - STAGE_TRANSITIONS["matching"]))
def test_set_stage_from_matching_rejects_every_other_target(target: str) -> None:
    state = {"stage": "matching"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "matching"


# "recommended" and "matched" are the third and fourth enforced stages (TWM-188).


@pytest.mark.parametrize("target", sorted(STAGE_TRANSITIONS["recommended"]))
def test_set_stage_from_recommended_accepts_its_documented_edges(target: str) -> None:
    state = {"stage": "recommended"}

    set_stage(state, target)

    assert state["stage"] == target


@pytest.mark.parametrize("target", sorted(VALID_STAGES - STAGE_TRANSITIONS["recommended"]))
def test_set_stage_from_recommended_rejects_every_other_target(target: str) -> None:
    state = {"stage": "recommended"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "recommended"


@pytest.mark.parametrize("target", sorted(STAGE_TRANSITIONS["matched"]))
def test_set_stage_from_matched_accepts_its_documented_edges(target: str) -> None:
    state = {"stage": "matched"}

    set_stage(state, target)

    assert state["stage"] == target


@pytest.mark.parametrize("target", sorted(VALID_STAGES - STAGE_TRANSITIONS["matched"]))
def test_set_stage_from_matched_rejects_every_other_target(target: str) -> None:
    state = {"stage": "matched"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "matched"


# "planning" is the fifth enforced stage (TWM-188 item 8).


@pytest.mark.parametrize("target", sorted(STAGE_TRANSITIONS["planning"]))
def test_set_stage_from_planning_accepts_its_documented_edges(target: str) -> None:
    state = {"stage": "planning"}

    set_stage(state, target)

    assert state["stage"] == target


@pytest.mark.parametrize("target", sorted(VALID_STAGES - STAGE_TRANSITIONS["planning"]))
def test_set_stage_from_planning_rejects_every_other_target(target: str) -> None:
    state = {"stage": "planning"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "planning"


def test_set_stage_from_planning_rejects_planned_directly() -> None:
    # approve_plan now always requires a non-empty day_plan, which always
    # means stage is already "plan_ready" by the time it fires — a direct
    # planning -> planned write is no longer legal.
    state = {"stage": "planning"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, "planned")


# "plan_ready" and "planned" are the sixth and seventh enforced stages.


@pytest.mark.parametrize("target", sorted(STAGE_TRANSITIONS["plan_ready"]))
def test_set_stage_from_plan_ready_accepts_its_documented_edges(target: str) -> None:
    state = {"stage": "plan_ready"}

    set_stage(state, target)

    assert state["stage"] == target


@pytest.mark.parametrize("target", sorted(VALID_STAGES - STAGE_TRANSITIONS["plan_ready"]))
def test_set_stage_from_plan_ready_rejects_every_other_target(target: str) -> None:
    state = {"stage": "plan_ready"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "plan_ready"


@pytest.mark.parametrize("target", sorted(VALID_STAGES))
def test_set_stage_from_planned_rejects_everything_including_itself(target: str) -> None:
    state = {"stage": "planned"}

    with pytest.raises(InvalidTripCommandError):
        set_stage(state, target)

    assert state["stage"] == "planned"
