"""Place name -> IATA airport resolution (TWM-196).

Airport/IATA resolution is Backend data correctness, not UI presentation
(see Linear TWM-196's Product Rule). This module is the single place that
turns a traveler-visible city/place label into a validated IATA code before
any Travelpayouts/Aviasales call is made -- no caller downstream of this
module is allowed to guess or invent an IATA code.

Resolution order:

1. ``CURATED_FALLBACK`` for bounded MVP aliases and known ambiguous bare
   place names. Every fallback value is validated against the loaded
   OurAirports dataset before being trusted (never a bare caller-invented
   code).
2. OurAirports municipality match, unioned with an OurAirports ``keywords``
   match (e.g. "Bangalore" is a keyword alias on Kempegowda International's
   "Bengaluru" municipality record) -- this is the primary source per the
   Linear issue's Backend Implementation Direction. Candidates from either
   source are ranked together, preferring a record with scheduled passenger
   service over one without (this is what keeps an unrelated same-named
   airport abroad -- e.g. a small non-scheduled "Madras Municipal Airport"
   in Oregon, US -- from ever outranking Chennai's MAA, which only appears
   via the keyword alias), then by airport size class.
If neither source resolves a usable, currently-listed IATA code, this
returns ``None`` -- callers must treat that as a typed clarification/
unavailable outcome, never as licence to guess.
"""

from __future__ import annotations

from typing import Literal, Optional

from .dataset import TYPE_PREFERENCE, AirportRecord, load_dataset
from .fallback import CURATED_FALLBACK

AirportResolutionSource = Literal["ourairports", "curated_fallback"]
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


def resolve_airport(place: Optional[str]) -> Optional[AirportResolution]:
    if place is None:
        return None
    label = place.strip()
    if not label:
        return None

    key = label.casefold()
    dataset = load_dataset()

    fallback_iata = CURATED_FALLBACK.get(key)
    if fallback_iata is not None:
        record = dataset.by_iata.get(fallback_iata)
        if record is not None:
            return AirportResolution(
                input_label=label,
                iata=record.iata,
                airport_name=record.name,
                source="curated_fallback",
                confidence="low",
                lat=record.lat,
                lon=record.lon,
            )

    candidates: list[AirportRecord] = list(dataset.by_municipality.get(key, ()))
    candidates.extend(dataset.by_keyword.get(key, ()))
    if candidates:
        best = min(candidates, key=_sort_candidate)
        return AirportResolution(
            input_label=label,
            iata=best.iata,
            airport_name=best.name,
            source="ourairports",
            confidence="high" if best.scheduled_service else "low",
            lat=best.lat,
            lon=best.lon,
        )

    return None
