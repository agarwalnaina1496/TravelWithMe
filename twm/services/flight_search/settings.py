"""Immutable configuration boundary for the flight-search provider (TWM-145).

Mirrors twm/services/agent_engine/settings.py's load-from-property pattern,
but deliberately does NOT fail fast when the token is unset: TWM-144
established "provider not configured" as a valid, safe runtime outcome for
this boundary (see FlightSearchService), so app startup must not crash when
AVIASALES_API_TOKEN is absent — unlike AgentEngineSettings, which is
always required for its selected engine.

The token itself is never read from twm/shared/properties/properties.ini in
this repository — only from the AVIASALES_API_TOKEN environment
variable, via property_loader._get_raw_property's env-first lookup. No
token value (real or fake-but-realistic) is committed anywhere.
"""

from dataclasses import dataclass
from typing import Optional

from ...shared.properties import property_loader

_DEFAULT_TIMEOUT_SECONDS = 10
# TWM-196: India-first MVP — default to INR so a missing AVIASALES_CURRENCY
# override never silently shows USD to Indian travelers. Still overridable
# via the aviasales_currency property/env for a future non-India market.
_DEFAULT_CURRENCY = "INR"


@dataclass(frozen=True)
class FlightSearchSettings:
    api_token: Optional[str]
    partner_id: Optional[str]
    timeout_seconds: int
    currency: str

    @property
    def is_configured(self) -> bool:
        return bool(self.api_token)

    @classmethod
    def load(cls) -> "FlightSearchSettings":
        # get_string_property_with_default("aviasales_api_token", "") checks
        # os.getenv("AVIASALES_API_TOKEN") first, then properties.ini (which
        # deliberately does not define this key — see properties.ini comment).
        token = property_loader.get_string_property_with_default(
            "aviasales_api_token", ""
        ).strip()
        partner_id = property_loader.get_string_property_with_default(
            "aviasales_partner_id", ""
        ).strip()
        timeout_seconds = _positive_int(
            "aviasales_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS
        )
        currency = property_loader.get_string_property_with_default(
            "aviasales_currency", _DEFAULT_CURRENCY
        ).strip().upper()

        return cls(
            api_token=token or None,
            partner_id=partner_id or None,
            timeout_seconds=timeout_seconds,
            currency=currency,
        )


def _positive_int(key: str, default: int) -> int:
    try:
        value = property_loader.get_int_property_with_default(key, default)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key.upper()} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{key.upper()} must be a positive integer")
    return value
