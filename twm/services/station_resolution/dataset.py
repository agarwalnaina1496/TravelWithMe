"""Loads and indexes the bundled Indian Railways station dataset (TWM-230).

The bundled file (``data/indian_railway_stations.json``) is a filtered export
of the community-maintained, CC0-licensed dataset at
https://github.com/datameet/railways (``stations.json``) -- keeping only rows
that carry a real station ``code``, a ``name``, and resolvable coordinates.
Placeholder rows (codes like ``XX-...``/``YY-...``/``ZZ-...`` with no
geometry) are dropped at export time; they are not stations, just unresolved
timetable references in the upstream data.

Exported fields: ``code``, ``name``, ``state``, ``zone``, ``address``,
``lat``, ``lon`` -- a subset of the source GeoJSON's properties.

Loaded once per process and cached at module scope -- mirrors
``twm.services.airport_resolution.dataset``, a static, version-controlled
reference dataset, not something that changes at runtime."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_PATH = Path(__file__).parent / "data" / "indian_railway_stations.json"
logger = logging.getLogger(__name__)

# A station name containing one of these is presumptively the primary/major
# station for its city when several candidates share a name or an address
# city (e.g. "BANGALORE CITY JN" over "BANGALORE CANT" for bare
# "Bangalore") -- a real, if imperfect, signal in a dataset that carries no
# explicit station-category field the way OurAirports carries airport type.
_MAJOR_STATION_KEYWORDS = ("junction", "jn", "central", "terminus", "terminal", "city")


@dataclass(frozen=True)
class StationRecord:
    code: str
    name: str
    state: str
    zone: str
    address: str
    lat: float
    lon: float


@dataclass(frozen=True)
class StationDataset:
    by_code: dict[str, StationRecord]
    by_name: dict[str, tuple[StationRecord, ...]]
    by_address_city: dict[str, tuple[StationRecord, ...]]
    by_first_word: dict[str, tuple[StationRecord, ...]]


def _address_city(address: str) -> str:
    return address.split(",")[0].strip() if address else ""


def _first_word(name: str) -> str:
    stripped = name.strip()
    return re.split(r"\s+", stripped)[0] if stripped else ""


def _sort_key(record: StationRecord) -> tuple[int, int, str]:
    name = record.name.casefold()
    has_major_keyword = any(
        re.search(rf"\b{keyword}\b", name) for keyword in _MAJOR_STATION_KEYWORDS
    )
    return (0 if has_major_keyword else 1, len(name), record.code)


@lru_cache(maxsize=1)
def load_dataset() -> StationDataset:
    raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

    by_code: dict[str, StationRecord] = {}
    by_name: dict[str, list[StationRecord]] = {}
    by_address_city: dict[str, list[StationRecord]] = {}
    by_first_word: dict[str, list[StationRecord]] = {}

    for entry in raw:
        try:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            logger.warning(
                "Skipping station row with invalid coordinates: %s",
                entry.get("code") or entry.get("name") or "<unknown>",
            )
            continue

        record = StationRecord(
            code=entry["code"],
            name=entry["name"],
            state=entry.get("state") or "",
            zone=entry.get("zone") or "",
            address=entry.get("address") or "",
            lat=lat,
            lon=lon,
        )
        by_code[record.code] = record

        name_key = record.name.strip().casefold()
        if name_key:
            by_name.setdefault(name_key, []).append(record)

        address_city_key = _address_city(record.address).casefold()
        if address_city_key:
            by_address_city.setdefault(address_city_key, []).append(record)

        first_word_key = _first_word(name_key)
        if first_word_key:
            by_first_word.setdefault(first_word_key, []).append(record)

    return StationDataset(
        by_code=by_code,
        by_name={key: tuple(sorted(records, key=_sort_key)) for key, records in by_name.items()},
        by_address_city={
            key: tuple(sorted(records, key=_sort_key)) for key, records in by_address_city.items()
        },
        by_first_word={
            key: tuple(sorted(records, key=_sort_key)) for key, records in by_first_word.items()
        },
    )


def best_match(records: tuple[StationRecord, ...]) -> "StationRecord | None":
    """The single best candidate from a name/address-city/first-word match
    group. Records are pre-sorted by ``_sort_key`` at load time, preferring a
    name that reads like a primary/major station."""

    return records[0] if records else None
