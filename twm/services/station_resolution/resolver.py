"""Place name -> Indian Railways station-code resolution (TWM-230).

Mirrors ``twm.services.airport_resolution.resolver`` exactly, including its
resolution order and its "never guess" contract: a caller downstream of this
module is never allowed to invent a station code, and a route this data
cannot confidently place returns ``None`` -- a typed "not resolvable"
outcome, not a fabricated fallback.

Resolution order:

1. ``CURATED_OVERRIDES`` for major cities whose bundled station *name*
   doesn't read as the city name, so a bare match would miss or pick a
   smaller same-city station over the primary interchange.
2. Bundled-dataset exact station-name match -- the primary source. Handles
   the common hubless-town/railhead case directly (a railhead is usually
   named after the town it serves, e.g. Falna, Abu Road, Pathankot).
3. Bundled-dataset address-city match, then first-word-of-name match, for
   places the exact-name index misses.
4. ``CURATED_FALLBACK`` for true gaps after the dataset has had its chance.

If none of these resolve a usable, currently-bundled station code, this
returns ``None``.
"""

from __future__ import annotations

from typing import Literal, Optional

from .dataset import StationRecord, best_match, load_dataset
from .fallback import CURATED_FALLBACK, CURATED_OVERRIDES

StationResolutionSource = Literal["curated_override", "dataset", "curated_fallback"]
StationResolutionConfidence = Literal["high", "low"]


class StationResolution:
    __slots__ = ("input_label", "code", "station_name", "source", "confidence", "lat", "lon")

    def __init__(
        self,
        *,
        input_label: str,
        code: str,
        station_name: str,
        source: StationResolutionSource,
        confidence: StationResolutionConfidence,
        lat: float,
        lon: float,
    ) -> None:
        self.input_label = input_label
        self.code = code
        self.station_name = station_name
        self.source = source
        self.confidence = confidence
        self.lat = lat
        self.lon = lon


def _from_record(
    *,
    label: str,
    record: StationRecord,
    source: StationResolutionSource,
    confidence: StationResolutionConfidence,
) -> StationResolution:
    return StationResolution(
        input_label=label,
        code=record.code,
        station_name=record.name,
        source=source,
        confidence=confidence,
        lat=record.lat,
        lon=record.lon,
    )


def _curated_resolution(
    *,
    label: str,
    code: Optional[str],
    source: StationResolutionSource,
) -> Optional[StationResolution]:
    if code is None:
        return None

    record = load_dataset().by_code.get(code)
    if record is None:
        return None

    return _from_record(label=label, record=record, source=source, confidence="low")


def resolve_station(place: Optional[str]) -> Optional[StationResolution]:
    if place is None:
        return None
    label = place.strip()
    if not label:
        return None

    key = label.casefold()
    dataset = load_dataset()

    override = _curated_resolution(
        label=label,
        code=CURATED_OVERRIDES.get(key),
        source="curated_override",
    )
    if override is not None:
        return override

    exact = best_match(dataset.by_name.get(key, ()))
    if exact is not None:
        return _from_record(label=label, record=exact, source="dataset", confidence="high")

    fuzzy = best_match(dataset.by_address_city.get(key, ())) or best_match(
        dataset.by_first_word.get(key, ())
    )
    if fuzzy is not None:
        return _from_record(label=label, record=fuzzy, source="dataset", confidence="low")

    return _curated_resolution(
        label=label,
        code=CURATED_FALLBACK.get(key),
        source="curated_fallback",
    )
