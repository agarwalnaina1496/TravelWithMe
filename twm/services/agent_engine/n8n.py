"""n8n webhook implementation of the agent-engine contract."""

from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from ...prompts import load_prompt_release
from ...schemas import MeridianAgentOutput, ScoutAgentOutput
from ...shared.properties import property_loader
from .contracts import AgentExecution


class N8NAgentEngine:
    def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return self._execute("scout", "n8n_scout_webhook_url", trip_state, message)

    def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        return self._execute(
            "meridian", "n8n_meridian_webhook_url", trip_state, message
        )

    def _execute(
        self,
        agent: str,
        property_key: str,
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
        response = self._forward(
            agent,
            property_key,
            payload,
        )
        return AgentExecution(response=response, prompt_release=release)

    def _forward(
        self, agent: str, property_key: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            url = property_loader.get_string_property(property_key)
            token = property_loader.get_string_property("n8n_webhook_token")
            if (
                property_loader.get_environment() == "prod"
                and urlparse(url).scheme != "https"
            ):
                raise ValueError("Production n8n webhooks require HTTPS")
            if not token.strip():
                raise ValueError("n8n webhook authentication is required")
        except Exception:
            state_delta = {}
            if agent == "meridian":
                state_delta = {
                    "matcher_state": {
                        "conversation_context": {"awaiting": None}
                    }
                }
            return {
                "status": "HARD_FAIL",
                "message": "The agent service is unavailable.",
                "state_delta": state_delta,
                "options": [],
            }

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                url,
                json=payload,
                headers={"X-TWM-Webhook-Token": token},
            )
            response.raise_for_status()
            return response.json()
