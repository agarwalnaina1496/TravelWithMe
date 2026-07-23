"""n8n webhook adapter returning raw model completions."""

import httpx

from .contracts import (
    AgentAdapterError,
    AgentAdapterTimeoutError,
    AgentInvocation,
    AgentInvocationResult,
    AgentName,
)
from .settings import AgentEngineSettings


class N8NAgentAdapter:
    def __init__(
        self, settings: AgentEngineSettings, http_client: httpx.AsyncClient
    ) -> None:
        self._settings = settings
        self._http_client = http_client

    async def invoke(
        self, agent: AgentName, invocation: AgentInvocation
    ) -> AgentInvocationResult:
        urls = {
            "scout": self._settings.n8n_scout_webhook_url,
            "meridian": self._settings.n8n_meridian_webhook_url,
        }
        try:
            response = await self._http_client.post(
                urls[agent] or "",
                json={
                    "system_prompt": invocation.system_prompt,
                    "user_prompt": invocation.user_prompt,
                    "output_schema": invocation.output_schema,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise AgentAdapterTimeoutError(
                f"{agent} n8n invocation timed out",
                component="n8n",
                failure_stage="invocation",
                error_type=type(error).__name__,
                detail=str(error).strip() or "n8n did not respond before the timeout",
            ) from error
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            raise AgentAdapterError(
                f"{agent} n8n invocation failed",
                component="n8n",
                failure_stage="upstream_http",
                error_type=type(error).__name__,
                detail=f"n8n returned HTTP {status_code}",
                upstream_status_code=status_code,
            ) from error
        except httpx.RequestError as error:
            raise AgentAdapterError(
                f"{agent} n8n invocation failed",
                component="n8n",
                failure_stage="upstream_connection",
                error_type=type(error).__name__,
                detail=str(error).strip() or "n8n connection failed",
            ) from error
        except ValueError as error:
            raise AgentAdapterError(
                f"{agent} n8n returned invalid JSON",
                component="n8n",
                failure_stage="response_decode",
                error_type=type(error).__name__,
                detail="n8n returned a response that was not valid JSON",
            ) from error

        raw_output = payload.get("raw_output") if isinstance(payload, dict) else None
        if not isinstance(raw_output, str):
            raise AgentAdapterError(
                f"{agent} n8n response did not contain raw_output",
                component="n8n",
                failure_stage="response_contract",
                error_type="N8NResponseContractError",
                detail="n8n response did not contain a string raw_output",
            )
        return AgentInvocationResult(raw_output=raw_output)
