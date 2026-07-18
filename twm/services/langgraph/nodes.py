"""Reusable nodes shared by Scout and Meridian graphs."""

import logging
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from .state import AgentGraphState
from ...security import frame_untrusted_payload


logger = logging.getLogger("uvicorn.error")


class PrepareAgentInputNode:
    """Convert the engine-neutral graph input into model messages."""

    def __call__(self, state: AgentGraphState) -> dict[str, Any]:
        payload = {
            "trip_state": state["trip_state"],
            "message": state["message"],
        }
        return {
            "messages": [
                SystemMessage(content=state["prompt"]),
                HumanMessage(
                    content=frame_untrusted_payload(
                        payload["trip_state"], payload["message"]
                    )
                ),
            ]
        }


class InvokeStructuredModelNode:
    """Invoke one dependency-injected structured-output model."""

    def __init__(self, structured_model: Any) -> None:
        self._structured_model = structured_model

    async def __call__(self, state: AgentGraphState) -> dict[str, Any]:
        return {
            "model_result": await self._structured_model.ainvoke(state["messages"])
        }


class ParseStructuredOutputNode(ABC):
    """Template method for validated structured-output parsing."""

    agent_name: str

    def __call__(self, state: AgentGraphState) -> dict[str, dict[str, Any]]:
        result = state["model_result"]
        parsed = result.get("parsed")
        if result.get("parsing_error") is not None or parsed is None:
            logger.warning("%s returned malformed structured output", self.agent_name)
            return {"response": self.malformed_response()}
        if isinstance(parsed, BaseModel):
            return {
                "response": parsed.model_dump(mode="json", exclude_none=True)
            }
        if isinstance(parsed, dict):
            return {"response": parsed}
        logger.warning("%s returned unsupported structured output", self.agent_name)
        return {"response": self.malformed_response()}

    @abstractmethod
    def malformed_response(self) -> dict[str, Any]:
        """Return the agent-specific infrastructure failure response."""
