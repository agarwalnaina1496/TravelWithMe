"""Nodes owned exclusively by the Scout graph."""

from typing import Any

from ..nodes import InvokeStructuredModelNode, ParseStructuredOutputNode


class InvokeScoutNode(InvokeStructuredModelNode):
    """Invoke the structured Scout model."""


class ParseScoutOutputNode(ParseStructuredOutputNode):
    """Validate and convert the Scout model result to the engine envelope."""

    agent_name = "Scout"

    @staticmethod
    def malformed_response() -> dict[str, Any]:
        return {
            "message": "I had trouble formatting that response. Please try again.",
            "state_delta": {},
            "intent": None,
        }
