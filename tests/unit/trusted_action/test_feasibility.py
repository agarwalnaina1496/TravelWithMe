"""Pure-function coverage for the deterministic trip-feasibility calculator
(TWM-195 root-fix rewrite). No classifier, no LLM, no agent-engine call --
``assess_trip_feasibility`` is a plain synchronous function over two plain
strings.
"""

import logging

from twm.services.trusted_action.feasibility import assess_trip_feasibility


def test_degenerate_route_returns_empty_modes_never_none():
    same_city = assess_trip_feasibility("Paris", "paris")
    assert same_city.modes == []

    blank_destination = assess_trip_feasibility("Somewhere", "")
    assert blank_destination.modes == []


def test_unknown_city_pair_returns_completely_empty_modes_fail_closed(caplog):
    # Regression guard: an unknown pair must return modes: [] -- never
    # partially feasible, and never conflated with "everything feasible"
    # (the original TWM-195 bug).
    caplog.set_level(logging.WARNING)
    result = assess_trip_feasibility("Nowhere Mapped", "Somewhere Else Unmapped")
    assert result.modes == []
    assert "Could not resolve origin city for feasibility: Nowhere Mapped" in caplog.text
    assert "Could not resolve destination city for feasibility: Somewhere Else Unmapped" in caplog.text


def test_one_known_one_unknown_city_still_returns_empty_modes(caplog):
    # Only one side missing from the bounded table is still "unknown" for
    # the pair -- Backend must not partially assess using only one side.
    caplog.set_level(logging.WARNING)
    result = assess_trip_feasibility("Delhi", "Somewhere Totally Unmapped")
    assert result.modes == []
    assert "Could not resolve destination city for feasibility: Somewhere Totally Unmapped" in caplog.text


def test_short_hop_route_excludes_flight_but_includes_train_bus_drive():
    # Bhubaneswar -> Puri (~60km): a local/short-hop route where flight is
    # route-absurd (Linear TWM-195 acceptance criterion).
    result = assess_trip_feasibility("Bhubaneswar", "Puri")
    modes = {entry.mode for entry in result.modes}
    assert "flight" not in modes
    assert modes == {"train", "bus", "drive"}


def test_very_short_hop_route_excludes_flight_puri_to_konark():
    # Puri -> Konark (~35km): the other short-hop example from the Linear
    # issue's calibration set.
    result = assess_trip_feasibility("Puri", "Konark")
    modes = {entry.mode for entry in result.modes}
    assert "flight" not in modes
    assert modes == {"train", "bus", "drive"}


def test_medium_distance_route_includes_flight_train_and_bus():
    # Bangalore -> Mangalore (~352km): the story's own multi-mode
    # acceptance example -- flight, train, and bus must all be returned.
    result = assess_trip_feasibility("Bangalore", "Mangalore")
    modes = {entry.mode for entry in result.modes}
    assert {"flight", "train", "bus"} <= modes
    # Comfortably under the drive threshold too.
    assert "drive" in modes


def test_resolver_only_city_pair_outside_old_static_table_gets_modes():
    # TWM-210: Shimla was not in feasibility.py's old hand-written
    # coordinate table but is resolvable through the shared airport resolver.
    result = assess_trip_feasibility("Delhi", "Shimla")
    assert result.modes
    assert {entry.mode for entry in result.modes} >= {"train", "bus"}


def test_long_distance_route_excludes_drive_but_includes_flight():
    # Delhi -> Chennai is well beyond the drive threshold but a normal
    # domestic flight distance.
    result = assess_trip_feasibility("Delhi", "Chennai")
    modes = {entry.mode for entry in result.modes}
    assert "drive" not in modes
    assert "flight" in modes
    assert {"train", "bus"} <= modes


def test_returned_modes_carry_general_guidance_verification_and_computed_source():
    result = assess_trip_feasibility("Bangalore", "Mangalore")
    assert result.modes
    for entry in result.modes:
        assert entry.status == "feasible"
        assert entry.duration_source == "computed"
        assert entry.verification is not None
        assert entry.verification.status == "GENERAL_GUIDANCE"
        assert entry.estimated_distance_km is not None


def test_no_duplicate_modes_returned():
    result = assess_trip_feasibility("Bangalore", "Mangalore")
    modes = [entry.mode for entry in result.modes]
    assert len(modes) == len(set(modes))


def test_assess_trip_feasibility_always_returns_a_real_assessment_never_none():
    # Callers must never need to handle a None return anymore.
    for origin, destination in [("Paris", "paris"), ("Nowhere", "Also Nowhere"), ("Delhi", "Agra")]:
        result = assess_trip_feasibility(origin, destination)
        assert result is not None


def test_distance_fallback_yields_train_bus_without_flight_for_an_unresolvable_hub():
    # TWM-215: a rail-only gateway hub with no resolvable airport still gets
    # train + bus (+ drive within range) via Atlas's long_haul_distance_km,
    # instead of the fail-closed empty modes.
    result = assess_trip_feasibility("Bengaluru", "Unmapped Rail Junction", long_haul_distance_km=420)
    modes = {entry.mode for entry in result.modes}
    assert modes == {"train", "bus", "drive"}


def test_distance_fallback_excludes_drive_when_the_ballpark_is_long():
    result = assess_trip_feasibility("Bengaluru", "Unmapped Far Junction", long_haul_distance_km=1200)
    modes = {entry.mode for entry in result.modes}
    assert modes == {"train", "bus"}  # no flight (no airport), no drive (too far)


def test_distance_fallback_is_ignored_when_both_cities_resolve():
    with_fallback = assess_trip_feasibility("Bangalore", "Mangalore", long_haul_distance_km=10)
    without = assess_trip_feasibility("Bangalore", "Mangalore")
    assert {m.mode for m in with_fallback.modes} == {m.mode for m in without.modes}
    assert "flight" in {m.mode for m in with_fallback.modes}


def test_non_positive_distance_fallback_still_fails_closed():
    assert assess_trip_feasibility("Bengaluru", "Unmapped Place", long_haul_distance_km=0).modes == []
    assert assess_trip_feasibility("Bengaluru", "Unmapped Place", long_haul_distance_km=-5).modes == []
