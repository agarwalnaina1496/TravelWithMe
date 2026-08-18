"""Immutable configuration boundary for trusted-action SEARCH_REDIRECT
resolvers (TWM-131).

Mirrors twm/services/flight_search/settings.py's load-from-property
pattern: every tracking/affiliate value here is optional and, when unset,
still produces a valid (non-affiliate-tracked) URL rather than failing app
startup or a request. No real credential value (or a fake-but-realistic
placeholder) is ever committed here.

Two distinct tracking identities are modelled, deliberately not shared:

- ``ixigo_affiliate_id``: ixigo's own affiliate program (EarnKaro/Cuelinks
  category-wise payouts, confirmed this session) — a separate account from
  Travelpayouts. Read from the ``IXIGO_AFFILIATE_ID`` environment variable
  only (key ``ixigo_affiliate_id``), never from properties.ini. Chosen name
  deliberately does not collide with any existing
  twm/shared/properties/properties.ini key.
- ``travelpayouts_marker``: the same Travelpayouts "marker" account already
  used by the Aviasales adapter (twm/services/flight_search/aviasales.py)
  for hotellook/booking_com/agoda deep links — reused, not duplicated, via
  constructor injection from ``FlightSearchSettings.partner_id`` at
  call-site wiring time (see twm/main.py). This module does not re-read the
  ``aviasales_partner_id`` property key itself.
"""

from dataclasses import dataclass
from typing import Optional

from ...shared.properties import property_loader


@dataclass(frozen=True)
class TrustedActionSettings:
    ixigo_affiliate_id: Optional[str]
    travelpayouts_marker: Optional[str]

    @classmethod
    def load(cls, *, travelpayouts_marker: Optional[str] = None) -> "TrustedActionSettings":
        ixigo_affiliate_id = property_loader.get_string_property_with_default(
            "ixigo_affiliate_id", ""
        ).strip()
        return cls(
            ixigo_affiliate_id=ixigo_affiliate_id or None,
            travelpayouts_marker=travelpayouts_marker or None,
        )
