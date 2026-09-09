"""TWM-217 / TWM-227: the bounded best-effort parse of trip_context.travel_dates."""

from datetime import date

from twm.services.trip_view.trip_dates import compose_trip_dates

# A fixed "today" so every year-resolution case is deterministic.
_TODAY = date(2026, 6, 15)


def _dates(raw, day_count=5, today=_TODAY):
    return compose_trip_dates({"travel_dates": raw}, day_count, today=today)


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


# --- TWM-227: yearless day/month resolved against `today` ---


def test_future_day_month_range_no_year_resolves_to_current_year():
    result = _dates("26–28 Sept", day_count=3)
    assert result.precision == "exact"
    assert result.departure == "2026-09-26"
    assert result.return_ == "2026-09-28"  # day_count 3
    assert result.source == "conversational"


def test_future_month_day_range_no_year_resolves_to_current_year():
    result = _dates("Sept 26-28", day_count=1)
    assert (result.precision, result.departure) == ("exact", "2026-09-26")


def test_future_single_day_month_no_year_resolves_to_current_year():
    result = _dates("26 September", day_count=4)
    assert result.precision == "exact"
    assert result.departure == "2026-09-26"
    assert result.return_ == "2026-09-29"


def test_past_day_month_no_year_is_unresolved_with_a_clean_label():
    result = _dates("26–28 Feb", day_count=3)
    assert result.precision == "none"
    assert result.label == "Feb 26–28"
    assert result.departure is None and result.month is None


def test_past_day_month_no_year_never_rolls_to_next_year():
    # Feb is behind 2026-06-15; the composer does not silently make it 2027.
    assert _dates("Feb 26").departure is None


def test_cross_year_range_no_year_spans_the_boundary():
    result = _dates("Dec 30 – Jan 2", day_count=4)
    assert result.precision == "exact"
    assert result.departure == "2026-12-30"
    assert result.return_ == "2027-01-02"


def test_cross_year_range_day_month_order():
    result = _dates("30 Dec - 2 Jan", day_count=4)
    assert (result.precision, result.departure, result.return_) == ("exact", "2026-12-30", "2027-01-02")


def test_future_bare_month_no_year_is_month_precision_current_year():
    result = _dates("October")
    assert result.precision == "month"
    assert result.month == "2026-10"


def test_past_bare_month_no_year_is_a_clean_none():
    result = _dates("March")
    assert result.precision == "none"
    assert result.label == "March"
    assert result.month is None


def test_bare_month_with_qualifier_words_is_left_verbatim():
    result = _dates("sometime in March")
    assert result.precision == "none"
    assert result.label == "sometime in March"  # no "you mentioned:" prefix
    assert result.source == "conversational"


def test_mid_march_is_none_never_a_guessed_day():
    result = _dates("mid-March")
    assert result.precision == "none"
    assert result.departure is None and result.month is None


def test_season_text_renders_verbatim_with_no_prefix():
    result = _dates("sometime in winter")
    assert result.precision == "none"
    assert result.label == "sometime in winter"


def test_flexible_and_empty_are_a_clean_none():
    for raw in ("flexible", "not sure yet", "", "   ", None, 42):
        result = compose_trip_dates({"travel_dates": raw}, 5, today=_TODAY)
        assert result.precision == "none"
        assert result.label is None
        assert result.source == "none"


def test_missing_key_is_none():
    assert compose_trip_dates({}, 5, today=_TODAY).precision == "none"


def test_today_defaults_to_the_real_date_when_not_injected():
    # No `today=` — exercises the date.today() fallback branch.
    result = compose_trip_dates({"travel_dates": "flexible"}, 5)
    assert result.precision == "none"
