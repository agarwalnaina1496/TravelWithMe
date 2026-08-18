"""Static IATA carrier-code -> airline-name lookup (TWM-145).

The Aviasales Data API discloses only a 2-letter IATA carrier code (e.g.
"6E"), never a full airline name. IATA codes are a small, stable,
publicly-documented set that changes only rarely (a carrier rebrand or
shutdown), so a static table is appropriate here rather than a live
lookup service. Scoped to carriers relevant to India routes, TWM's
current market; unknown codes resolve to None rather than a guess.
"""

_IATA_CARRIER_NAMES: dict[str, str] = {
    "6E": "IndiGo",
    "AI": "Air India",
    "UK": "Vistara",
    "SG": "SpiceJet",
    "G8": "Go First",
    "I5": "AirAsia India",
    "QP": "Akasa Air",
    "9W": "Jet Airways",
    "EK": "Emirates",
    "EY": "Etihad Airways",
    "QR": "Qatar Airways",
    "SQ": "Singapore Airlines",
    "TG": "Thai Airways",
    "LH": "Lufthansa",
    "BA": "British Airways",
    "AF": "Air France",
    "KL": "KLM",
    "TK": "Turkish Airlines",
    "CX": "Cathay Pacific",
    "MH": "Malaysia Airlines",
}


def airline_name_for_code(code: str) -> str | None:
    return _IATA_CARRIER_NAMES.get(code.strip().upper())
