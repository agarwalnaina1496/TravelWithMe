"""Nodes owned exclusively by the Meridian graph."""

from typing import Any

from ..nodes import InvokeStructuredModelNode, ParseStructuredOutputNode


class InvokeMeridianNode(InvokeStructuredModelNode):
    """Invoke the structured Meridian model."""


class ParseMeridianOutputNode(ParseStructuredOutputNode):
    """Validate and convert the Meridian result to the engine envelope."""

    agent_name = "Meridian"

    @staticmethod
    def malformed_response() -> dict[str, Any]:
        return {
            "status": "HARD_FAIL",
            "message": "I had trouble formatting that response. Please try again.",
            "state_delta": {},
            "options": [],
        }
