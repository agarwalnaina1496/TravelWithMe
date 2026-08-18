"""Travelpayouts Data API adapter (TWM-145).

Wraps GET /v1/prices/cheap — the simplest cached cheapest-price lookup on
the Data API tier, matching TWM-144's single-offer-per-route contract shape
best. Stop-count enrichment via GET /v1/prices/calendar (which does carry a
`transfers` field) is a natural follow-up, not required now — see
twm/schemas/flight_search.py's NormalizedFlightOffer.stop_count docstring.

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


class TravelpayoutsAdapter:
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
        depart_date: Optional[date],
        return_date: Optional[date],
        currency: str,
    ) -> tuple[list[dict], datetime]:
        """Return the raw per-entry dicts for `destination_iata` plus the
        time Backend received the response (used as price_found_at — this
        endpoint discloses no provider-side "found at" timestamp of its
        own, only `expires_at`, which maps to offer_expires_at)."""

        params: dict[str, str] = {
            "origin": origin_iata,
            "destination": destination_iata,
            "currency": currency.lower(),
        }
        if depart_date is not None:
            params["depart_date"] = depart_date.isoformat()
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
                "travelpayouts cheap-prices lookup timed out",
                component="travelpayouts",
                failure_stage="invocation",
                error_type=type(error).__name__,
                detail=str(error).strip()
                or "travelpayouts did not respond before the timeout",
            ) from error
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            raise FlightProviderError(
                "travelpayouts cheap-prices lookup failed",
                component="travelpayouts",
                failure_stage="upstream_http",
                error_type=type(error).__name__,
                detail=f"travelpayouts returned HTTP {status_code}",
                upstream_status_code=status_code,
            ) from error
        except httpx.RequestError as error:
            raise FlightProviderError(
                "travelpayouts cheap-prices lookup failed",
                component="travelpayouts",
                failure_stage="upstream_connection",
                error_type=type(error).__name__,
                detail=str(error).strip() or "travelpayouts connection failed",
            ) from error
        except ValueError as error:
            raise FlightProviderError(
                "travelpayouts returned invalid JSON",
                component="travelpayouts",
                failure_stage="response_decode",
                error_type=type(error).__name__,
                detail="travelpayouts returned a response that was not valid JSON",
            ) from error

        received_at = datetime.now(timezone.utc)

        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise FlightProviderError(
                "travelpayouts reported an unsuccessful lookup",
                component="travelpayouts",
                failure_stage="response_contract",
                error_type="TravelpayoutsResponseContractError",
                detail="travelpayouts response did not report success=true",
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise FlightProviderError(
                "travelpayouts response did not contain a data object",
                component="travelpayouts",
                failure_stage="response_contract",
                error_type="TravelpayoutsResponseContractError",
                detail="travelpayouts response 'data' field was missing or not an object",
            )

        entries = data.get(destination_iata.upper(), {})
        if not isinstance(entries, dict):
            entries = {}
        return [entry for entry in entries.values() if isinstance(entry, dict)], received_at
