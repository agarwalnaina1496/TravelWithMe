"""Agent-engine abstraction and n8n transport implementation."""

from dataclasses import dataclass
from typing import Any, Optional, Protocol

import httpx

from ..prompts import PromptRelease, load_prompt_release
from ..shared.properties import property_loader


@dataclass(frozen=True)
class AgentExecution:
    response: dict[str, Any]
    prompt_release: PromptRelease


class AgentEngine(Protocol):
    def scout(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...

    def meridian(
        self, trip_state: dict[str, Any], message: Optional[str]
    ) -> AgentExecution:
        ...


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
        response = self._forward(
            property_key,
            {
                "prompt": release.content,
                "trip_state": trip_state,
                "message": message,
            },
        )
        return AgentExecution(response=response, prompt_release=release)

    def _forward(self, property_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            url = property_loader.get_string_property(property_key)
        except Exception:
            return {
                "status": "HARD_FAIL",
                "message": f"{property_key} is not configured.",
                "state_delta": {},
                "options": [],
            }

        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()


def get_agent_engine() -> AgentEngine:
    engine_name = property_loader.get_string_property_with_default(
        "agent_engine", "n8n"
    ).strip().lower()

    if engine_name == "n8n":
        return N8NAgentEngine()

    if engine_name == "langgraph":
        raise ValueError(
            "AGENT_ENGINE=langgraph is reserved for TWM-56 and is not "
            "available in the runtime-foundation release"
        )

    raise ValueError(
        f"Unsupported AGENT_ENGINE: {engine_name or '<empty>'}. "
        "Expected n8n or langgraph."
    )
