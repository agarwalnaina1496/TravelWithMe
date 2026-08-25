"""Curated Backend fallback for MVP-known places the bundled OurAirports
municipality/keyword lookup cannot resolve on its own (TWM-196).

This is deliberately small and bounded -- the same "do not grow this into a
general-purpose source of truth" judgement call already applied to
``twm/services/trusted_action/feasibility.py``'s ``_KNOWN_CITY_COORDINATES``.
OurAirports is the primary source; an entry belongs here only when a real
MVP itinerary city has no usable OurAirports municipality/keyword match
(e.g. a common colloquial spelling OurAirports does not carry as a
keyword). Every value here must still be a real, currently-valid IATA code
present in the bundled dataset -- this map never invents an airport, it only
adds an extra name -> IATA alias.
"""

from __future__ import annotations

# name (casefold) -> IATA code. Keep alphabetized by key for reviewability.
# Every entry here is a name that the OurAirports municipality/keyword
# lookup does not resolve at all -- not an override for a name it already
# resolves (resolver.py's scheduled-service-first ranking already handles
# ambiguous cases like "Madras" without needing an entry here).
CURATED_FALLBACK: dict[str, str] = {
    "pondicherry": "PNY",
    "trivandrum": "TRV",
}
