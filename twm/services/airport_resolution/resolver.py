"""Place name -> IATA airport resolution (TWM-196).

Airport/IATA resolution is Backend data correctness, not UI presentation
(see Linear TWM-196's Product Rule). This module is the single place that
turns a traveler-visible city/place label into a validated IATA code before
any Travelpayouts/Aviasales call is made -- no caller downstream of this
module is allowed to guess or invent an IATA code.

Resolution order:

1. ``CURATED_OVERRIDES`` for bounded MVP aliases whose bare place name is
   known to match an unrelated global airport in OurAirports. Every override
   value is validated against the loaded OurAirports dataset before being
   trusted (never a bare caller-invented code).
2. OurAirports municipality match, unioned with an OurAirports ``keywords``
   match (e.g. "Bangalore" is a keyword alias on Kempegowda International's
   "Bengaluru" municipality record) -- this is the primary source per the
   Linear issue's Backend Implementation Direction. Candidates from either
   source are ranked together, preferring a record with scheduled passenger
   service over one without (this is what keeps an unrelated same-named
   airport abroad -- e.g. a small non-scheduled "Madras Municipal Airport"
   in Oregon, US -- from ever outranking Chennai's MAA, which only appears
   via the keyword alias), then by airport size class.
3. ``CURATED_FALLBACK`` for true MVP gaps after OurAirports has had the
   first chance to resolve the label.
If neither source resolves a usable, currently-listed IATA code, this
returns ``None`` -- callers must treat that as a typed clarification/
unavailable outcome, never as licence to guess.
"""

from __future__ import annotations

from typing import Literal, Optional

from .dataset import TYPE_PREFERENCE, AirportRecord, load_dataset
from .fallback import CURATED_FALLBACK, CURATED_OVERRIDES

AirportResolutionSource = Literal["ourairports", "curated_fallback", "curated_override"]
AirportResolutionConfidence = Literal["high", "low"]


class AirportResolution:
    __slots__ = ("input_label", "iata", "airport_name", "source", "confidence", "lat", "lon")

    def __init__(
        self,
        *,
        input_label: str,
        iata: str,
        airport_name: str,
        source: AirportResolutionSource,
        confidence: AirportResolutionConfidence,
        lat: float,
        lon: float,
    ) -> None:
        self.input_label = input_label
        self.iata = iata
        self.airport_name = airport_name
        self.source = source
        self.confidence = confidence
        self.lat = lat
        self.lon = lon


def _sort_candidate(record: AirportRecord) -> tuple[int, int, str]:
    return (
        0 if record.scheduled_service else 1,
        TYPE_PREFERENCE.get(record.type, 9),
        record.iata,
    )


def _from_record(
    *,
    label: str,
    record: AirportRecord,
    source: AirportResolutionSource,
    confidence: AirportResolutionConfidence,
) -> AirportResolution:
    return AirportResolution(
        input_label=label,
        iata=record.iata,
        airport_name=record.name,
        source=source,
        confidence=confidence,
        lat=record.lat,
        lon=record.lon,
    )


def _curated_resolution(
    *,
    label: str,
    iata: Optional[str],
    source: AirportResolutionSource,
) -> Optional[AirportResolution]:
    if iata is None:
        return None

    record = load_dataset().by_iata.get(iata)
    if record is None:
        return None

    return _from_record(
        label=label,
        record=record,
        source=source,
        confidence="low",
    )


def resolve_airport(place: Optional[str]) -> Optional[AirportResolution]:
    if place is None:
        return None
    label = place.strip()
    if not label:
        return None

    key = label.casefold()
    dataset = load_dataset()

    override = _curated_resolution(
        label=label,
        iata=CURATED_OVERRIDES.get(key),
        source="curated_override",
    )
    if override is not None:
        return override

    candidates: list[AirportRecord] = list(dataset.by_municipality.get(key, ()))
    candidates.extend(dataset.by_keyword.get(key, ()))
    if candidates:
        best = min(candidates, key=_sort_candidate)
        return _from_record(
            label=label,
            record=best,
            source="ourairports",
            confidence="high" if best.scheduled_service else "low",
        )

    return _curated_resolution(
        label=label,
        iata=CURATED_FALLBACK.get(key),
        source="curated_fallback",
    )
