"""Loads and indexes the bundled OurAirports dataset (TWM-196).

The bundled file (``data/ourairports_airports.json``) is a filtered export of
the public OurAirports ``airports.csv`` (https://ourairports.com/data/),
keeping only rows that carry a 3-letter ``iata_code`` -- every other airport
type (heliports, closed strips, ident-only entries with no IATA code) is
irrelevant to flight-search route resolution and would only bloat the bundle.
Exported fields: ``iata``, ``name``, ``municipality``, ``country``, ``type``,
``scheduled_service``, ``lat``, ``lon``, ``keywords`` -- a strict subset of
the source CSV's columns.

Loaded once per process and cached at module scope -- this is a static,
version-controlled reference dataset, not something that changes at
runtime."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA_PATH = Path(__file__).parent / "data" / "ourairports_airports.json"
logger = logging.getLogger(__name__)

# OurAirports ``type`` values in descending order of how confidently a bare
# city/place name should resolve to this airport when a municipality has more
# than one candidate (e.g. a city with both a large international airport and
# a small regional strip sharing the same municipality name).
TYPE_PREFERENCE = {
    "large_airport": 0,
    "medium_airport": 1,
    "small_airport": 2,
    "seaplane_base": 3,
    "heliport": 4,
    "closed": 5,
}


@dataclass(frozen=True)
class AirportRecord:
    iata: str
    name: str
    municipality: str
    country: str
    type: str
    scheduled_service: bool
    lat: float
    lon: float
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class AirportDataset:
    by_iata: dict[str, AirportRecord]
    by_municipality: dict[str, tuple[AirportRecord, ...]]
    by_keyword: dict[str, tuple[AirportRecord, ...]]


def _sort_key(record: AirportRecord) -> tuple[int, int, str]:
    return (
        0 if record.scheduled_service else 1,
        TYPE_PREFERENCE.get(record.type, 9),
        record.iata,
    )


@lru_cache(maxsize=1)
def load_dataset() -> AirportDataset:
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

    by_iata: dict[str, AirportRecord] = {}
    by_municipality: dict[str, list[AirportRecord]] = {}
    by_keyword: dict[str, list[AirportRecord]] = {}

    for entry in raw:
        keywords = tuple(
            keyword.strip()
            for keyword in (entry.get("keywords") or "").split(",")
            if keyword.strip()
        )
        try:
            lat = float(entry.get("lat"))
            lon = float(entry.get("lon"))
        except (TypeError, ValueError):
            logger.warning(
                "Skipping airport row with invalid coordinates: %s",
                entry.get("iata") or entry.get("name") or "<unknown>",
            )
            continue

        record = AirportRecord(
            iata=entry["iata"],
            name=entry["name"],
            municipality=entry.get("municipality") or "",
            country=entry.get("country") or "",
            type=entry.get("type") or "",
            scheduled_service=bool(entry.get("scheduled_service")),
            lat=lat,
            lon=lon,
            keywords=keywords,
        )
        by_iata[record.iata] = record

        municipality_key = record.municipality.strip().casefold()
        if municipality_key:
            by_municipality.setdefault(municipality_key, []).append(record)

        for keyword in keywords:
            keyword_key = keyword.casefold()
            by_keyword.setdefault(keyword_key, []).append(record)

    return AirportDataset(
        by_iata=by_iata,
        by_municipality={
            key: tuple(sorted(records, key=_sort_key))
            for key, records in by_municipality.items()
        },
        by_keyword={
            key: tuple(sorted(records, key=_sort_key))
            for key, records in by_keyword.items()
        },
    )


def best_match(records: tuple[AirportRecord, ...]) -> Optional[AirportRecord]:
    """The single best candidate from a municipality/keyword match group,
    preferring scheduled-passenger-service airports and larger airport
    types. Records are pre-sorted by ``_sort_key`` at load time, so this is
    just "first survivor with a usable IATA code"."""

    for record in records:
        if record.iata:
            return record
    return None
