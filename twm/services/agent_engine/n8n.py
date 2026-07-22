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

    @property
    def engine_name(self) -> str:
        return "n8n"

    def endpoint(self, agent: AgentName) -> str | None:
        return f"n8n:{agent}"

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
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as error:
            raise AgentAdapterTimeoutError(
                f"{agent} n8n invocation timed out"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise AgentAdapterError(f"{agent} n8n invocation failed") from error

        raw_output = payload.get("raw_output") if isinstance(payload, dict) else None
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise AgentAdapterError(
                f"{agent} n8n response did not contain raw_output"
            )
        return AgentInvocationResult(raw_output=raw_output)
