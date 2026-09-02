"""Trip Board composition (TWM-202) — Atlas + Trusted Actions merge and
date-precision reconciliation, in isolation from the HTTP boundary."""

import logging
from uuid import uuid4

from twm.schemas.trusted_action import ModeFeasibility, TripFeasibilityAssessment
from twm.services.trip_board import TripBoardService

TRIP_ID = uuid4()


class FakeTrustedActionService:
    """Records calls; returns a canned assessment keyed by (origin, destination),
    defaulting to zero feasible modes for an unlisted pair."""

    def __init__(self, assessments: dict[tuple[str, str], TripFeasibilityAssessment] | None = None):
        self.assessments = assessments or {}
        self.calls: list[tuple[str, str]] = []

    def assess_feasibility(self, trip_id, origin, destination):
        self.calls.append((origin, destination))
        return self.assessments.get((origin, destination), TripFeasibilityAssessment(modes=[]))


def _reference():
    return {"status": "GENERAL_GUIDANCE"}


def _activity_item(title="Explore", location="Delhi", detail="Detail."):
    return {
        "kind": "ACTIVITY",
        "title": title,
        "location": location,
        "detail": detail,
        "reference": _reference(),
    }


def _stay_item(location="Chennai"):
    return {
        "kind": "STAY",
        "title": "Overnight stay",
        "location": location,
        "detail": "Stay overnight.",
        "reference": _reference(),
    }


def _travel_item(from_city, to_city, *, departure_date=None, departure_month=None, title=None):
    return {
        "kind": "TRAVEL",
        "title": title or f"Travel from {from_city} to {to_city}",
        "location": f"{from_city} to {to_city}",
        "detail": "Onward travel.",
        "from_city": from_city,
        "to_city": to_city,
        "departure_date": departure_date,
        "departure_month": departure_month,
        "reference": _reference(),
    }


def _start(*, date=None, month=None):
    """A booking_setup dict carrying just the calendar anchor."""
    if date is not None:
        return {"start": {"precision": "exact", "date": date}}
    return {"start": {"precision": "month", "month": month}}


def _pref(target_type, target_id, *, date=None, month=None):
    """A booking_setup dict carrying one per-entity search-date preference."""
    entry = {"precision": "exact", "date": date} if date is not None else {"precision": "month", "month": month}
    return {"search_prefs": {f"{target_type}s": {target_id: entry}}}


def _day(day_number, timeline):
    return {
        "day_number": day_number,
        "title": f"Day {day_number}",
        "primary_location": "Delhi",
        "summary": "Summary.",
        "notes": [
            {
                "category": "Weather",
                "title": "Carry layers",
                "detail": "Evenings may be cool.",
                "reference": _reference(),
            }
        ],
        "timeline": timeline,
    }


def test_full_data_happy_path_composes_gateway_leg_with_feasibility():
    modes = [
        ModeFeasibility(
            mode="flight", status="feasible", duration_source="computed",
            reason="Long distance.", verification={"status": "GENERAL_GUIDANCE"},
        )
    ]
    trusted_action = FakeTrustedActionService({("Delhi", "Chennai"): TripFeasibilityAssessment(modes=modes)})
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [_day(1, [_travel_item("Delhi", "Chennai"), _activity_item()])],
    }

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    leg = board.days[0].items[0]
    assert leg.is_gateway_leg is True
    assert leg.feasible_modes == modes
    activity = board.days[0].items[1]
    assert activity.is_gateway_leg is False
    assert activity.feasible_modes is None


def test_gateway_leg_matching_uses_resolved_city_aliases():
    modes = [
        ModeFeasibility(
            mode="flight", status="feasible", duration_source="computed",
            reason="Long distance.", verification={"status": "GENERAL_GUIDANCE"},
        )
    ]
    trusted_action = FakeTrustedActionService({("Bengaluru", "Chennai"): TripFeasibilityAssessment(modes=modes)})
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [_day(1, [_travel_item("Bengaluru", "Chennai")])],
    }

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Bangalore"})

    leg = board.days[0].items[0]
    assert leg.is_gateway_leg is True
    assert leg.feasible_modes == modes
    assert trusted_action.calls == [("Bengaluru", "Chennai")]


def test_non_gateway_leg_never_gets_feasibility_computed():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Agra")]),
            _day(2, [_travel_item("Agra", "Jaipur")]),
        ],
    }

    # origin_city never matches either leg's from_city/to_city.
    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Mumbai"})

    for day in board.days:
        for item in day.items:
            assert item.is_gateway_leg is False
            assert item.feasible_modes is None
    assert trusted_action.calls == []


def test_unresolved_gateway_city_logs_warning_and_fails_closed(caplog):
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [_day(1, [_travel_item("Atlantis", "Chennai")])],
    }

    caplog.set_level(logging.WARNING)
    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    leg = board.days[0].items[0]
    assert leg.is_gateway_leg is False
    assert leg.feasible_modes is None
    assert trusted_action.calls == []
    assert "Could not resolve trip-board city: Atlantis" in caplog.text


def test_item_own_date_takes_precedence_and_is_passed_through_exactly():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [_day(1, [_travel_item("Delhi", "Chennai", departure_date="2026-05-01")])],
    }

    board = service.build(
        TRIP_ID, 1, final_itinerary,
        {"origin_city": "Delhi"}, _start(date="2026-09-01"),
    )

    leg = board.days[0].items[0]
    assert leg.date_precision == "exact"
    assert leg.departure_date == "2026-05-01"
    assert leg.date_source == "itinerary"


def test_every_leg_gets_its_own_day_date_from_the_calendar_anchor():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Chennai")]),
            _day(2, [_travel_item("Chennai", "Delhi")]),
        ],
    }

    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, _start(date="2026-05-01"),
    )

    outbound_leg = board.days[0].items[0]
    inbound_leg = board.days[1].items[0]
    assert outbound_leg.date_precision == "exact"
    assert outbound_leg.departure_date == "2026-05-01"
    assert outbound_leg.date_source == "anchor"
    assert inbound_leg.is_gateway_leg is True
    assert inbound_leg.date_precision == "exact"
    assert inbound_leg.departure_date == "2026-05-02"
    assert inbound_leg.date_source == "anchor"


def test_legs_are_flexible_when_no_calendar_anchor_is_set():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Chennai")]),
            _day(2, [_travel_item("Chennai", "Delhi")]),
        ],
    }

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    for leg in (board.days[0].items[0], board.days[1].items[0]):
        assert leg.date_precision == "flexible"
        assert leg.departure_date is None
        assert leg.date_source == "none"


def test_month_anchor_applies_to_any_dateless_leg_gateway_or_internal():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Agra")]),  # internal leg
            _day(2, [_travel_item("Agra", "Delhi")]),  # gateway leg (return)
        ],
    }

    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, _start(month="2026-05"),
    )

    internal_leg = board.days[0].items[0]
    gateway_leg = board.days[1].items[0]
    assert internal_leg.date_precision == "month"
    assert internal_leg.departure_month == "2026-05"
    assert internal_leg.date_source == "anchor"
    assert gateway_leg.date_precision == "month"
    assert gateway_leg.departure_month == "2026-05"


def test_transport_search_pref_overrides_the_anchor_for_that_leg_only():
    service = TripBoardService(FakeTrustedActionService())
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Agra")]),
            _day(2, [_travel_item("Agra", "Delhi")]),
        ],
    }
    override_leg_id = f"{TRIP_ID}:2:0"

    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"},
        {**_start(date="2026-05-01"), **_pref("transport", override_leg_id, date="2026-05-09")},
    )

    assert board.days[0].items[0].departure_date == "2026-05-01"
    assert board.days[0].items[0].date_source == "anchor"
    assert board.days[1].items[0].departure_date == "2026-05-09"
    assert board.days[1].items[0].date_source == "override"


def test_no_date_information_at_all_is_flexible_not_fabricated():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {"days": [_day(1, [_travel_item("Delhi", "Chennai")])]}

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    leg = board.days[0].items[0]
    assert leg.date_precision == "flexible"
    assert leg.departure_date is None
    assert leg.departure_month is None


def test_non_travel_item_never_gets_a_date_precision():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {"days": [_day(1, [_activity_item()])]}

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    assert board.days[0].items[0].date_precision is None


def test_exact_trip_start_computes_one_calendar_date_per_day(caplog):
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [
            _day(1, [_activity_item()]),
            _day(2, [_stay_item(), _activity_item(title="Marina Beach")]),
            _day(3, [_activity_item(title="Museum")]),
        ]
    }

    caplog.set_level(logging.INFO)
    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, _start(date="2026-05-30"),
    )

    assert [day.date for day in board.days] == [
        "2026-05-30",
        "2026-05-31",
        "2026-06-01",
    ]
    computed = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "be.trip_board.day_date.computed"
    ]
    assert [record.day_number for record in computed] == [1, 2, 3]
    assert all(record.trip_id == str(TRIP_ID) for record in computed)


def test_stay_segments_group_consecutive_same_location_stays_with_exact_dates():
    service = TripBoardService(FakeTrustedActionService())
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Jaipur"), _stay_item("Jaipur")]),
            _day(2, [_activity_item(location="Jaipur"), _stay_item("Jaipur")]),
            _day(3, [_travel_item("Jaipur", "Agra"), _stay_item("Agra")]),
            _day(4, [_activity_item(location="Agra"), _stay_item("Agra")]),
            _day(5, [_travel_item("Agra", "Delhi")]),
        ]
    }

    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, _start(date="2026-05-01"),
    )

    assert [(segment.location, segment.nights) for segment in board.stay_segments] == [
        ("Jaipur", 2),
        ("Agra", 2),
    ]
    assert board.stay_segments[0].checkin_date == "2026-05-01"
    assert board.stay_segments[0].checkout_date == "2026-05-03"
    assert board.stay_segments[0].date_source == "anchor"
    assert board.stay_segments[1].checkin_date == "2026-05-03"
    assert board.stay_segments[1].checkout_date == "2026-05-05"
    assert board.days[0].items[1].id in board.stay_segments[0].board_item_ids
    assert board.days[2].items[1].id in board.stay_segments[1].board_item_ids


def test_stay_search_pref_sets_checkin_and_derives_checkout_from_nights():
    service = TripBoardService(FakeTrustedActionService())
    final_itinerary = {
        "days": [
            _day(1, [_stay_item("Agra")]),
            _day(2, [_stay_item("Agra")]),
        ]
    }
    segment_id = f"{TRIP_ID}:stay:1:2:agra"

    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"},
        {**_start(date="2026-05-01"), **_pref("stay", segment_id, date="2026-06-10")},
    )

    segment = board.stay_segments[0]
    assert segment.checkin_date == "2026-06-10"
    assert segment.checkout_date == "2026-06-12"
    assert segment.date_precision == "exact"
    assert segment.date_source == "override"


def test_stay_segments_do_not_fabricate_exact_dates_without_exact_trip_start():
    service = TripBoardService(FakeTrustedActionService())
    final_itinerary = {"days": [_day(1, [_stay_item("Jaipur")]), _day(2, [_stay_item("Jaipur")])]}

    month_board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, _start(month="2026-05"),
    )
    flexible_board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    assert month_board.stay_segments[0].date_precision == "month"
    assert month_board.stay_segments[0].departure_month == "2026-05"
    assert month_board.stay_segments[0].checkin_date is None
    assert month_board.stay_segments[0].checkout_date is None
    assert month_board.stay_segments[0].date_source == "anchor"
    assert flexible_board.stay_segments[0].date_precision == "flexible"
    assert flexible_board.stay_segments[0].checkin_date is None
    assert flexible_board.stay_segments[0].checkout_date is None
    assert flexible_board.stay_segments[0].date_source == "none"


def test_stay_segments_skip_stays_without_known_location():
    service = TripBoardService(FakeTrustedActionService())
    final_itinerary = {
        "days": [
            _day(1, [_stay_item("Jaipur")]),
            _day(2, [_stay_item(None)]),
            _day(3, [_stay_item("   ")]),
            _day(4, [_stay_item("Agra")]),
        ]
    }

    board = service.build(
        TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, _start(date="2026-05-01"),
    )

    assert [(segment.location, segment.start_day_number, segment.end_day_number) for segment in board.stay_segments] == [
        ("Jaipur", 1, 1),
        ("Agra", 4, 4),
    ]
    assert all(segment.location != "None" for segment in board.stay_segments)


def test_non_exact_booking_precision_does_not_compute_day_dates():
    service = TripBoardService(FakeTrustedActionService())
    final_itinerary = {"days": [_day(1, [_stay_item()]), _day(2, [_activity_item()])]}

    for booking_setup in (_start(month="2026-05"), None):
        board = service.build(
            TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"}, booking_setup,
        )

        assert [day.date for day in board.days] == [None, None]


def test_response_shape_is_limited_to_current_frontend_allowlist():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [_day(1, [_activity_item(title="Visit Ram Jhula", location="Rishikesh", detail="Walk across the bridge.")])],
    }

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    assert set(board.days[0].model_dump().keys()) == {"day_number", "date", "items"}
    assert set(board.days[0].items[0].model_dump().keys()) == {
        "id",
        "kind",
        "location",
        "from_city",
        "to_city",
        "is_gateway_leg",
        "feasible_modes",
        "date_precision",
        "departure_date",
        "departure_month",
        "date_source",
    }
    assert set(board.model_dump().keys()) == {"version", "days", "stay_segments"}


# TWM-209: stable, deterministic per-item id.
def test_item_id_is_stable_across_two_build_calls_for_the_same_version():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [_day(1, [_travel_item("Delhi", "Chennai"), _activity_item()])],
    }

    first = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})
    second = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    assert [item.id for item in first.days[0].items] == [item.id for item in second.days[0].items]


def test_item_id_differs_across_items_within_a_day_and_across_days():
    trusted_action = FakeTrustedActionService()
    service = TripBoardService(trusted_action)
    final_itinerary = {
        "days": [
            _day(1, [_travel_item("Delhi", "Chennai"), _activity_item()]),
            _day(2, [_activity_item(title="Different day, same shape")]),
        ],
    }

    board = service.build(TRIP_ID, 1, final_itinerary, {"origin_city": "Delhi"})

    all_ids = [item.id for day in board.days for item in day.items]
    assert len(all_ids) == len(set(all_ids))
