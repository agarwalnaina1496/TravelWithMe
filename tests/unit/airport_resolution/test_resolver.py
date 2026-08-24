"""Unit tests for twm.services.airport_resolution (TWM-196)."""

from twm.services.airport_resolution import resolve_airport


def test_exact_municipality_match_resolves_high_confidence():
    result = resolve_airport("Bhubaneswar")
    assert result is not None
    assert result.iata == "BBI"
    assert result.source == "ourairports"
    assert result.confidence == "high"


def test_case_and_whitespace_insensitive_municipality_match():
    result = resolve_airport("  bhubaneswar  ")
    assert result is not None
    assert result.iata == "BBI"


def test_keyword_alias_resolves_bangalore_to_blr():
    result = resolve_airport("Bangalore")
    assert result is not None
    assert result.iata == "BLR"
    assert result.source == "ourairports"


def test_scheduled_service_candidate_wins_over_unrelated_same_name_airport():
    # "Madras" only municipality-matches an unrelated, non-scheduled small
    # airport in the US; the real Chennai/Madras airport (MAA) only appears
    # via the keywords alias. Scheduled-service ranking must still resolve
    # this to MAA, not the US strip.
    result = resolve_airport("Madras")
    assert result is not None
    assert result.iata == "MAA"


def test_curated_fallback_used_for_a_known_mvp_gap():
    result = resolve_airport("Pondicherry")
    assert result is not None
    assert result.iata == "PNY"
    assert result.source == "curated_fallback"
    assert result.confidence == "low"


def test_curated_fallback_is_case_insensitive():
    result = resolve_airport("TRIVANDRUM")
    assert result is not None
    assert result.iata == "TRV"
    assert result.source == "curated_fallback"


def test_unresolvable_place_returns_none_not_a_guess():
    assert resolve_airport("Nowhereville") is None


def test_blank_and_none_input_return_none():
    assert resolve_airport("") is None
    assert resolve_airport("   ") is None
    assert resolve_airport(None) is None


def test_input_label_preserves_the_original_caller_text_trimmed():
    result = resolve_airport("  Bhubaneswar  ")
    assert result is not None
    assert result.input_label == "Bhubaneswar"
