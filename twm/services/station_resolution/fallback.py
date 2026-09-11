"""Curated Backend aliases for station resolution (TWM-230).

Deliberately small and bounded, mirroring
``twm.services.airport_resolution.fallback``. The bundled dataset is the
primary source; an entry belongs here only when a real itinerary place name
would otherwise resolve to a real-but-wrong station in the bundled data
(``CURATED_OVERRIDES``), or genuinely does not resolve at all
(``CURATED_FALLBACK``). Every value here must be a real, currently-bundled
station code -- these maps never invent a station, they only add bounded
name -> code aliases. ``resolver.py`` validates every entry against the
loaded dataset before trusting it.
"""

from __future__ import annotations

# Checked before the dataset match, for major cities where the bundled
# dataset's own station *name* does not read as the city name (so a bare
# name/first-word match either misses entirely or lands on a smaller,
# same-city station instead of the primary interchange) -- e.g. Bengaluru's
# main terminus is literally named "BANGALORE CITY JN", not "Bengaluru".
# Every code here was checked against the bundled dataset at authoring time.
# Keep alphabetized by key.
CURATED_OVERRIDES: dict[str, str] = {
    "agra": "AGC",
    "ahmedabad": "ADI",
    "amritsar": "ASR",
    "bangalore": "SBC",
    "bengaluru": "SBC",
    "bhopal": "BPL",
    "chandigarh": "CDG",
    "chennai": "MAS",
    "coimbatore": "CBE",
    "dehradun": "DDN",
    "delhi": "NDLS",
    "guwahati": "GHY",
    "hyderabad": "SC",
    "indore": "INDB",
    "jodhpur": "JU",
    "kanpur": "CNB",
    "kochi": "ERS",
    "kolkata": "HWH",
    "lucknow": "LKO",
    "mumbai": "CSTM",
    "nagpur": "NGP",
    "patna": "PNBE",
    "pune": "PUNE",
    "surat": "ST",
    "varanasi": "BSB",
    "visakhapatnam": "VSKP",
}

# Checked after the dataset match, for true gaps -- a bare place name with no
# usable exact/address-city/first-word match at all. Empty for now: every
# hubless-town/railhead case exercised this session (Falna, Abu Road,
# Pathankot, Kangra, Jaipur, New Delhi, Udaipur) resolved directly from the
# bundled dataset without needing an alias. Add an entry here only once a
# real gap is confirmed against the loaded dataset, the same way
# ``airport_resolution``'s fallback grew.
CURATED_FALLBACK: dict[str, str] = {}
