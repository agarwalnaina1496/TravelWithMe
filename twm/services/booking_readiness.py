"""Shared route-readiness policy for every Backend service that resolves a
provider/redirect action from a route + optional date/traveler request
(``flight_search``, ``trusted_action``).

Extracted (TWM-215) after the identical "is a route field genuinely
missing" check had drifted out of sync between two independently
maintained ``missing_required_fields`` functions: the departure-date fix
(TWM-196) had already established that a field a downstream resolver
treats as optional must never gate the whole request, but that lesson
lived only in ``flight_search``'s copy of the check. When the same fix was
needed for ``traveler_count``, ``trusted_action``'s copy had to be found
and patched separately, from scratch. A caller now composes this single
source of truth for the route-completeness gate instead of re-deriving it,
so the two can never independently drift again.

This expresses only the one hard-requirement pattern every such service
actually needs -- a real route, and a return date once round-trip is
declared. It deliberately says nothing about date precision or traveler
count, both already established (TWM-196/TWM-215) as resolver-optional
everywhere reachable: a caller must never reintroduce either as a blocking
requirement here without first confirming the resolver it feeds genuinely
requires it, the same way `resolvers.py`/`build_query_params` were checked
before departure_date and traveler_count were each declared optional.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RouteReadiness:
    origin_missing: bool
    destination_missing: bool
    return_date_missing: bool


def route_readiness(
    *,
    has_origin: bool,
    has_destination: bool,
    is_round_trip: bool,
    has_return_date: bool,
) -> RouteReadiness:
    """Pure, deterministic route-completeness judgment. ``has_origin`` lets
    a caller whose domain has no origin concept at all (e.g. a stay/hotel
    search) pass ``True`` unconditionally, rather than this function
    needing to know about domains it has no business knowing about."""

    return RouteReadiness(
        origin_missing=not has_origin,
        destination_missing=not has_destination,
        return_date_missing=is_round_trip and not has_return_date,
    )
