"""TWM-217: the bounded best-effort parse of trip_context.travel_dates."""

from twm.services.trip_view.trip_dates import compose_trip_dates


def _dates(raw, day_count=5):
    return compose_trip_dates({"travel_dates": raw}, day_count)


def test_iso_date_range_becomes_exact_departure_and_computed_return():
    result = _dates("2026-03-12 to 2026-03-17", day_count=5)
    assert result.precision == "exact"
    assert result.departure == "2026-03-12"
    # itinerary length is authoritative — 5 days => return = departure + 4
    assert result.return_ == "2026-03-16"
    assert result.source == "conversational"


def test_single_iso_date_computes_return_from_day_count():
    result = _dates("2026-03-12", day_count=5)
    assert (result.precision, result.departure, result.return_) == ("exact", "2026-03-12", "2026-03-16")


def test_month_and_day_with_explicit_year_is_exact():
    result = _dates("March 12-17, 2026", day_count=3)
    assert result.precision == "exact"
    assert result.departure == "2026-03-12"
    assert result.return_ == "2026-03-14"  # day_count 3 wins over the stated 17th


def test_a_stated_end_that_disagrees_with_day_count_loses_to_the_itinerary():
    # "leaving the 12th" + a 5-day plan => return is the 16th, not any stated end.
    result = _dates("12 March 2026", day_count=5)
    assert result.return_ == "2026-03-16"


def test_month_and_year_only_is_month_precision():
    result = _dates("March 2026")
    assert result.precision == "month"
    assert result.month == "2026-03"
    assert result.label == "March 2026"


def test_year_month_iso_is_month_precision():
    assert _dates("2026-03").month == "2026-03"


def test_bare_month_with_no_year_is_none_with_verbatim_label():
    result = _dates("sometime in March")
    assert result.precision == "none"
    assert result.label == "you mentioned: sometime in March"
    assert result.source == "conversational"


def test_mid_march_is_none_never_a_guessed_day():
    result = _dates("mid-March")
    assert result.precision == "none"
    assert result.departure is None and result.month is None


def test_flexible_and_empty_are_a_clean_none():
    for raw in ("flexible", "not sure yet", "", "   ", None, 42):
        result = compose_trip_dates({"travel_dates": raw}, 5)
        assert result.precision == "none"
        assert result.label is None
        assert result.source == "none"


def test_missing_key_is_none():
    assert compose_trip_dates({}, 5).precision == "none"
