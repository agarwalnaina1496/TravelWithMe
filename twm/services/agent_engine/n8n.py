"""n8n webhook implementation of the agent-engine contract."""

from typing import Any, Optional
import httpx

from ...prompts import load_prompt_release
from ...schemas import MeridianAgentOutput, ScoutAgentOutput
from .contracts import AgentExecution
from .settings import AgentEngineSettings


class N8NAgentEngine:
    def __init__(
        self, settings: AgentEngineSettings, http_client: httpx.AsyncClient
    ) -> None:
        self._settings = settings
        self._http_client = http_client

    async def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return await self._execute(
            "scout", self._settings.n8n_scout_webhook_url, trip_state, message
        )

    async def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return await self._execute(
            "meridian", self._settings.n8n_meridian_webhook_url, trip_state, message
        )

    async def _execute(
        self,
        agent: str,
        url: str | None,
        trip_state: dict[str, Any],
        message: Optional[str],
    ) -> AgentExecution:
        release = load_prompt_release(agent)
        payload = {
            "prompt": release.content,
            "trip_state": trip_state,
            "message": message,
        }
        output_schemas = {
            "scout": ScoutAgentOutput,
            "meridian": MeridianAgentOutput,
        }
        payload["output_schema"] = output_schemas[agent].model_json_schema()
        response = await self._forward(url, payload)
        return AgentExecution(response=response, prompt_release=release)

    async def _forward(
        self, url: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._http_client.post(
            url or "",
            json=payload,
        )
        response.raise_for_status()
        return _canonical_webhook_response(response.json())


def _canonical_webhook_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, dict) and isinstance(raw.get("json"), dict):
        raw = raw["json"]
    if isinstance(raw, dict) and isinstance(raw.get("output"), dict):
        raw = raw["output"]
    return raw if isinstance(raw, dict) else {}
