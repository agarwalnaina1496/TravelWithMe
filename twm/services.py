from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

import httpx

from .prompts import PromptRelease, load_prompt_release
from .shared.properties import property_loader


@dataclass(frozen=True)
class AgentExecution:
    response: Dict[str, Any]
    prompt_release: PromptRelease


class AgentEngine(Protocol):
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> AgentExecution:
        ...

    def meridian(self, trip_state: Dict[str, Any]) -> AgentExecution:
        ...


class N8NAgentEngine:
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> AgentExecution:
        release = load_prompt_release("scout")
        response = self._forward(
            "n8n_scout_webhook_url",
            {
                "prompt": release.content,
                "trip_state": trip_state,
                "message": message,
            },
        )
        return AgentExecution(response=response, prompt_release=release)

    def meridian(self, trip_state: Dict[str, Any]) -> AgentExecution:
        release = load_prompt_release("meridian")
        response = self._forward(
            "n8n_meridian_webhook_url",
            {
                "prompt": release.content,
                "trip_state": trip_state,
            },
        )
        return AgentExecution(response=response, prompt_release=release)

    def _forward(self, property_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
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
    ).lower()

    if engine_name == "n8n":
        return N8NAgentEngine()

    raise ValueError(f"Unsupported agent_engine: {engine_name}")
