"""Unit tests for twm.services.flight_search.settings (TWM-196 currency
default fix)."""

from twm.services.flight_search.settings import FlightSearchSettings


def test_default_currency_is_inr_for_the_india_first_mvp(monkeypatch) -> None:
    monkeypatch.delenv("AVIASALES_CURRENCY", raising=False)
    settings = FlightSearchSettings.load()
    assert settings.currency == "INR"


def test_currency_env_override_still_takes_precedence(monkeypatch) -> None:
    monkeypatch.setenv("AVIASALES_CURRENCY", "usd")
    settings = FlightSearchSettings.load()
    assert settings.currency == "USD"
