"""Trip-context merge semantics shared across Scout, Meridian, and Guide."""

from twm.services.trip_commands.state import merge_trip_context


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
