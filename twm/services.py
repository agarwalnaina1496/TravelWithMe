import os
from typing import Any, Dict, Optional, Protocol

import httpx


class n8nEngine(Protocol):
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> Dict[str, Any]:
        ...

    def meridian(self, trip_context: Dict[str, Any]) -> Dict[str, Any]:
        ...


class DefaultN8NEngine:
    def scout(self, trip_state: Dict[str, Any], message: Optional[str]) -> Dict[str, Any]:
        return self._forward("N8N_SCOUT_WEBHOOK_URL", {"trip_state": trip_state, "message": message})

    def meridian(self, trip_context: Dict[str, Any]) -> Dict[str, Any]:
        return self._forward("N8N_MERIDIAN_WEBHOOK_URL", {"trip_context": trip_context})

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


def get_n8n_engine() -> n8nEngine:
    return DefaultN8NEngine()
