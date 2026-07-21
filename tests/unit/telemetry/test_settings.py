import pytest

from twm.shared.properties import property_loader
from twm.telemetry import PayloadMode, TelemetrySettings


def test_settings_load_environment_controls(monkeypatch) -> None:
    values = {
        "telemetry_enabled": "true",
        "telemetry_payload_mode": "full",
        "telemetry_max_field_size": "2048",
    }
    monkeypatch.setattr(
        property_loader,
        "get_string_property_with_default",
        lambda key, default: values.get(key, default),
    )
    monkeypatch.setattr(
        property_loader,
        "get_int_property_with_default",
        lambda key, default: int(values.get(key, default)),
    )
    monkeypatch.setattr(property_loader, "get_environment", lambda: "prod")

    loaded = TelemetrySettings.load()

    assert loaded == TelemetrySettings(True, "prod", PayloadMode.FULL, 2048)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("telemetry_enabled", "sometimes", "TELEMETRY_ENABLED"),
        ("telemetry_payload_mode", "verbose", "TELEMETRY_PAYLOAD_MODE"),
    ],
)
def test_settings_reject_invalid_values(monkeypatch, key, value, message) -> None:
    monkeypatch.setattr(
        property_loader,
        "get_string_property_with_default",
        lambda requested, default: value if requested == key else default,
    )
    with pytest.raises(ValueError, match=message):
        TelemetrySettings.load()
