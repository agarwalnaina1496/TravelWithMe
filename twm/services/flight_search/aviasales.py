"""Aviasales Data API adapter (TWM-145).

Wraps two Data-API endpoints, both served by the Travelpayouts platform
that Aviasales' consumer product runs on (the API host is
api.travelpayouts.com regardless of the Aviasales branding used elsewhere
in this module):

- GET /v1/prices/cheap: the primary lookup. Keyed by destination, with
  multiple cached candidate entries per route — this is what lets the
  service return several ranked offers per search, matching the product's
  "our pick + alternatives" comparison pattern. It never discloses a stop
  count.
- GET /v1/prices/calendar: a best-effort enrichment call for the single
  requested date. It discloses `transfers` (stop count) but only for one
  cached entry per date, not the same candidate set as /v1/prices/cheap.
  fetch_stop_count_hint() returns that one (airline, flight_number) ->
  stop_count pairing (or None on any failure) so the service can enrich a
  matching /v1/prices/cheap entry without making stop-count enrichment a
  hard dependency of the search succeeding at all.

Auth is via the `X-Access-Token` header, never a query parameter, so the
token can never leak into a logged URL. Mirrors
twm/services/agent_engine/n8n.py's N8NAgentAdapter shape: constructor DI of
settings + httpx.AsyncClient, typed error mapping for
timeout/HTTP-status/connection/decode failures. No raw upstream payload is
ever attached to a raised error (see errors.py).
"""

from datetime import date, datetime, timezone
from typing import Optional

import httpx

from .errors import FlightProviderError, FlightProviderTimeoutError
from .settings import FlightSearchSettings

_BASE_URL = "https://api.travelpayouts.com"
_CHEAP_PRICES_PATH = "/v1/prices/cheap"
_CALENDAR_PATH = "/v1/prices/calendar"


class AviasalesAdapter:
    def __init__(
        self, settings: FlightSearchSettings, http_client: httpx.AsyncClient
    ) -> None:
        self._settings = settings
        self._http_client = http_client

    async def fetch_cheapest_prices(
        self,
        *,
        origin_iata: str,
        destination_iata: str,
        depart_date: Optional[str],
        return_date: Optional[date],
        currency: str,
    ) -> tuple[list[dict], datetime]:
        """Return the raw per-entry dicts for `destination_iata` plus the
        time Backend received the response (used as price_found_at — this
        endpoint discloses no provider-side "found at" timestamp of its
        own, only `expires_at`, which maps to offer_expires_at).

        depart_date is a pre-formatted string, not a date object, because
        this single endpoint honestly serves three precisions (TWM-196):
        an exact "YYYY-MM-DD" day, a "YYYY-MM" month window, or omitted
        entirely for the route's latest cached prices with no date
        commitment. The caller (FlightSearchService, via
        calculations.resolve_date_precision) decides which of those to
        send; this adapter has no date-precision policy of its own."""

        params: dict[str, str] = {
            "origin": origin_iata,
            "destination": destination_iata,
            "currency": currency.lower(),
        }
        if depart_date is not None:
            params["depart_date"] = depart_date
        if return_date is not None:
            params["return_date"] = return_date.isoformat()
        if self._settings.partner_id:
            params["marker"] = self._settings.partner_id

        headers = {"X-Access-Token": self._settings.api_token or ""}

        try:
            response = await self._http_client.get(
                f"{_BASE_URL}{_CHEAP_PRICES_PATH}",
                params=params,
                headers=headers,
                timeout=self._settings.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise FlightProviderTimeoutError(
                "aviasales cheap-prices lookup timed out",
                component="aviasales",
                failure_stage="invocation",
                error_type=type(error).__name__,
                detail=str(error).strip()
                or "aviasales did not respond before the timeout",
            ) from error
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            raise FlightProviderError(
                "aviasales cheap-prices lookup failed",
                component="aviasales",
                failure_stage="upstream_http",
                error_type=type(error).__name__,
                detail=f"aviasales returned HTTP {status_code}",
                upstream_status_code=status_code,
            ) from error
        except httpx.RequestError as error:
            raise FlightProviderError(
                "aviasales cheap-prices lookup failed",
                component="aviasales",
                failure_stage="upstream_connection",
                error_type=type(error).__name__,
                detail=str(error).strip() or "aviasales connection failed",
            ) from error
        except ValueError as error:
            raise FlightProviderError(
                "aviasales returned invalid JSON",
                component="aviasales",
                failure_stage="response_decode",
                error_type=type(error).__name__,
                detail="aviasales returned a response that was not valid JSON",
            ) from error

        received_at = datetime.now(timezone.utc)

        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise FlightProviderError(
                "aviasales reported an unsuccessful lookup",
                component="aviasales",
                failure_stage="response_contract",
                error_type="AviasalesResponseContractError",
                detail="aviasales response did not report success=true",
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise FlightProviderError(
                "aviasales response did not contain a data object",
                component="aviasales",
                failure_stage="response_contract",
                error_type="AviasalesResponseContractError",
                detail="aviasales response 'data' field was missing or not an object",
            )

        entries = data.get(destination_iata.upper(), {})
        if not isinstance(entries, dict):
            entries = {}
        return [entry for entry in entries.values() if isinstance(entry, dict)], received_at

    async def fetch_stop_count_hint(
        self,
        *,
        origin_iata: str,
        destination_iata: str,
        depart_date: date,
        currency: str,
    ) -> Optional[tuple[str, str, int]]:
        """Best-effort (airline_code, flight_number, stop_count) for the
        requested date from /v1/prices/calendar, or None on any failure —
        stop-count enrichment is a nice-to-have, never a reason to fail the
        whole search. Only one entry exists per date on this endpoint, so
        this can enrich at most one of /v1/prices/cheap's candidate offers
        (matched by airline + flight_number in the caller)."""

        params: dict[str, str] = {
            "origin": origin_iata,
            "destination": destination_iata,
            "depart_date": depart_date.isoformat(),
            "calendar_type": "departure_date",
            "currency": currency.lower(),
        }
        headers = {"X-Access-Token": self._settings.api_token or ""}

        try:
            response = await self._http_client.get(
                f"{_BASE_URL}{_CALENDAR_PATH}",
                params=params,
                headers=headers,
                timeout=self._settings.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, ValueError):
            return None

        if not isinstance(payload, dict) or payload.get("success") is not True:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        entry = data.get(depart_date.isoformat())
        if not isinstance(entry, dict):
            return None

        airline = entry.get("airline")
        flight_number = entry.get("flight_number")
        transfers = entry.get("transfers")
        if airline is None or flight_number is None or not isinstance(transfers, int):
            return None
        return str(airline), str(flight_number), transfers
