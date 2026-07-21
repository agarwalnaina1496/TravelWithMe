"""Environment-backed, provider-neutral telemetry settings."""

from dataclasses import dataclass
from enum import Enum

from ..shared.properties import property_loader


class PayloadMode(str, Enum):
    OFF = "off"
    METADATA = "metadata"
    FULL = "full"


@dataclass(frozen=True)
class TelemetrySettings:
    enabled: bool
    environment: str
    payload_mode: PayloadMode
    max_field_size: int
    service: str = "travelwithme-backend"

    @classmethod
    def load(cls) -> "TelemetrySettings":
        enabled = _boolean_property("telemetry_enabled", False)
        raw_mode = property_loader.get_string_property_with_default(
            "telemetry_payload_mode", PayloadMode.METADATA.value
        ).strip().lower()
        try:
            payload_mode = PayloadMode(raw_mode)
        except ValueError as exc:
            raise ValueError(
                "TELEMETRY_PAYLOAD_MODE must be off, metadata, or full"
            ) from exc

        max_field_size = property_loader.get_int_property_with_default(
            "telemetry_max_field_size", 16_384
        )
        if max_field_size <= 0:
            raise ValueError("TELEMETRY_MAX_FIELD_SIZE must be a positive integer")

        return cls(
            enabled=enabled,
            environment=property_loader.get_environment(),
            payload_mode=payload_mode,
            max_field_size=max_field_size,
        )


def _boolean_property(key: str, default: bool) -> bool:
    raw = property_loader.get_string_property_with_default(
        key, str(default).lower()
    ).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key.upper()} must be a boolean")
