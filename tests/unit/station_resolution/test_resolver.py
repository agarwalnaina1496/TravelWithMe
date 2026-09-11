"""Unit tests for twm.services.station_resolution (TWM-230)."""

import json
import logging

from twm.services.station_resolution import resolve_station
from twm.services.station_resolution import dataset as dataset_module


def test_exact_name_match_resolves_a_railhead_high_confidence():
    # The station-shaped case Increment 1 was built around: Falna is the
    # railhead for hubless Sumerpur, and its bundled station name is the
    # bare town name -- an exact match, not an override.
    result = resolve_station("Falna")
    assert result is not None
    assert result.code == "FA"
    assert result.source == "dataset"
    assert result.confidence == "high"


def test_case_and_whitespace_insensitive_exact_match():
    result = resolve_station("  falna  ")
    assert result is not None
    assert result.code == "FA"


def test_curated_override_resolves_a_major_city_to_its_primary_terminus():
    # Bengaluru's main terminus is bundled as "BANGALORE CITY JN" -- a bare
    # name/first-word match would either miss or land on the smaller
    # "BANGALORE CANT" instead of the primary interchange.
    result = resolve_station("Bengaluru")
    assert result is not None
    assert result.code == "SBC"
    assert result.source == "curated_override"


def test_curated_override_is_case_insensitive():
    result = resolve_station("BANGALORE")
    assert result is not None
    assert result.code == "SBC"


def test_curated_overrides_cover_the_major_metros():
    assert resolve_station("Mumbai").code == "CSTM"
    assert resolve_station("Chennai").code == "MAS"
    assert resolve_station("Kolkata").code == "HWH"
    assert resolve_station("Delhi").code == "NDLS"
    assert resolve_station("Hyderabad").code == "SC"


def test_new_delhi_resolves_via_dataset_exact_match():
    result = resolve_station("New Delhi")
    assert result is not None
    assert result.code == "NDLS"
    assert result.source == "dataset"
    assert result.confidence == "high"


def test_dataset_address_city_fallback_resolves_udaipur():
    # No bundled station is literally named "Udaipur" (the main one is
    # "UDAIPUR CITY") -- the address-city / first-word fallback tier
    # resolves it without a curated entry.
    result = resolve_station("Udaipur")
    assert result is not None
    assert result.code == "UDZ"
    assert result.source == "dataset"
    assert result.confidence == "low"


def test_dataset_loader_skips_rows_with_invalid_coordinates(tmp_path, monkeypatch, caplog):
    data_path = tmp_path / "indian_railway_stations.json"
    data_path.write_text(
        json.dumps(
            [
                {"code": "BAD", "name": "Bad Coordinates", "state": "", "zone": "", "address": "", "lat": "", "lon": "77.0"},
                {"code": "GUD", "name": "Good Coordinates", "state": "", "zone": "", "address": "", "lat": "12.9", "lon": "77.6"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dataset_module, "_DATA_PATH", data_path)
    dataset_module.load_dataset.cache_clear()
    caplog.set_level(logging.WARNING)

    try:
        loaded = dataset_module.load_dataset()
    finally:
        dataset_module.load_dataset.cache_clear()

    assert "BAD" not in loaded.by_code
    assert loaded.by_code["GUD"].lat == 12.9
    assert loaded.by_code["GUD"].lon == 77.6
    assert "Skipping station row with invalid coordinates: BAD" in caplog.text


def test_unresolvable_place_returns_none_not_a_guess():
    # A hubless town with no station of its own and no curated alias --
    # exactly why a gateway hub exists for it in the first place.
    assert resolve_station("Sumerpur") is None
    assert resolve_station("Nowhereville") is None


def test_blank_and_none_input_return_none():
    assert resolve_station("") is None
    assert resolve_station("   ") is None
    assert resolve_station(None) is None


def test_input_label_preserves_the_original_caller_text_trimmed():
    result = resolve_station("  Falna  ")
    assert result is not None
    assert result.input_label == "Falna"
