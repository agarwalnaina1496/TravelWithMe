import os
import httpx
from .prompts import load_prompt
from typing import Any, Dict, Optional, Protocol

class AgentEngine(Protocol):
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> Dict[str, Any]:
        ...

    def meridian(self, trip_context: Dict[str, Any]) -> Dict[str, Any]:
        ...


class N8NAgentEngine:
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> Dict[str, Any]:
        return self._forward(
            "N8N_SCOUT_WEBHOOK_URL",
            {
                "prompt": load_prompt("scout"),
                "trip_state": trip_state,
                "message": message,
            },
        )

    def meridian(self, trip_context: Dict[str, Any]) -> Dict[str, Any]:
        return self._forward(
            "N8N_MERIDIAN_WEBHOOK_URL",
            {
                "prompt": load_prompt("meridian"),
                "trip_context": trip_context,
            },
        )

    def _forward(self, env_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = os.getenv(env_key)
        if not url:
            return {
                "status": "HARD_FAIL",
                "message": f"{env_key} is not configured.",
                "eliminating_constraints": [],
                "relaxation_suggestions": [],
                "surviving_destinations": [],
            }

        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()


def get_agent_engine() -> AgentEngine:
    engine_name = os.getenv("AGENT_ENGINE", "n8n").lower()

    if engine_name == "n8n":
        return N8NAgentEngine()

    raise ValueError(f"Unsupported AGENT_ENGINE: {engine_name}")
