"""Curated Backend aliases for MVP-known places (TWM-196).

This is deliberately small and bounded. OurAirports is the primary source;
an entry belongs here only when a real MVP itinerary place either has no
usable OurAirports municipality/keyword match, or when a bare Indian place
name would otherwise match an unrelated global airport. Every value here
must still be a real, currently-valid IATA code present in the bundled
dataset -- these maps never invent an airport, they only add bounded
name -> IATA aliases.
"""

from __future__ import annotations

# Checked before OurAirports only for names where the upstream dataset has a
# known wrong same-name match for TWM's India itinerary context.
CURATED_OVERRIDES: dict[str, str] = {
    # Bare "Puri" exists in OurAirports as an unrelated non-India airport,
    # but TWM itinerary usage means Puri, Odisha.
    "puri": "BBI",
}

# Checked after OurAirports for true gaps where the primary dataset does not
# resolve the bare traveler-facing place name. Keep alphabetized by key.
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
    "shimla": "SLV",
    "trivandrum": "TRV",
}
