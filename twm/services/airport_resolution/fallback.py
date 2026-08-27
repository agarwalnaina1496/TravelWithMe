"""Curated Backend fallback for MVP-known places the bundled OurAirports
municipality/keyword lookup cannot resolve on its own (TWM-196).

This is deliberately small and bounded. OurAirports is the primary source;
an entry belongs here only when a real MVP itinerary place has no usable
OurAirports municipality/keyword match, or when a bare Indian place name
would otherwise match an unrelated global airport. Every value here must
still be a real, currently-valid IATA code present in the bundled dataset --
this map never invents an airport, it only adds an extra name -> IATA alias.
"""

from __future__ import annotations

# name (casefold) -> IATA code. Keep alphabetized by key for reviewability.
# Most entries here are names that the OurAirports municipality/keyword
# lookup does not resolve at all. A few are explicit MVP-place overrides
# where a bare Indian itinerary place would otherwise resolve to an
# unrelated same-name airport abroad.
CURATED_FALLBACK: dict[str, str] = {
    # OurAirports' municipality for Indira Gandhi International is "New
    # Delhi" only — bare "Delhi" (the common gateway-city name travelers
    # actually use) has no municipality or keyword match at all. Confirmed
    # blocking via manual TWM-196 verification: Bangalore -> Delhi returned
    # clarification_needed on the destination side.
    "delhi": "DEL",
    # Same class of gap: OurAirports' Goa Dabolim keyword is the full phrase
    # "Goa Airport", not the bare "Goa" travelers use, and Jaisalmer
    # Airport's municipality is blank with keyword "Jaisalmer Air Force
    # Station" rather than a bare "Jaisalmer" alias.
    "goa": "GOI",
    "jaisalmer": "JSA",
    "konark": "BBI",
    "mangalore": "IXE",
    "ooty": "CJB",
    "pondicherry": "PNY",
    "puri": "BBI",
    "shimla": "SLV",
    "trivandrum": "TRV",
}
