import httpx
from .prompts import load_prompt
from .shared.properties import property_loader
from typing import Any, Dict, Optional, Protocol

class AgentEngine(Protocol):
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> Dict[str, Any]:
        ...

    def meridian(self, trip_context: Dict[str, Any]) -> Dict[str, Any]:
        ...


class N8NAgentEngine:
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> Dict[str, Any]:
        return self._forward(
            "n8n_scout_webhook_url",
            {
                "prompt": load_prompt("scout"),
                "trip_state": trip_state,
                "message": message,
            },
        )

    def meridian(self, trip_context: Dict[str, Any]) -> Dict[str, Any]:
        return self._forward(
            "n8n_meridian_webhook_url",
            {
                "prompt": load_prompt("meridian"),
                "trip_context": trip_context,
            },
        )

    def _forward(self, property_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            url = property_loader.get_string_property(property_key)
        except Exception:
            return {
                "status": "HARD_FAIL",
                "message": f"{property_key} is not configured.",
                "eliminating_constraints": [],
                "relaxation_suggestions": [],
                "surviving_destinations": [],
            }

        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            return response.json()


def get_agent_engine() -> AgentEngine:
    engine_name = property_loader.get_string_property_with_default("agent_engine", "n8n").lower()

    if engine_name == "n8n":
        return N8NAgentEngine()

    raise ValueError(f"Unsupported agent_engine: {engine_name}")
