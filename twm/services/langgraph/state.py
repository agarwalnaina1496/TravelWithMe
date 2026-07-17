"""Shared state contracts used by every agent graph."""

from typing import Any, Optional, TypedDict

from langchain_core.messages import BaseMessage


class AgentGraphInput(TypedDict):
    prompt: str
    trip_state: dict[str, Any]
    message: Optional[str]


class AgentGraphState(AgentGraphInput, total=False):
    messages: list[BaseMessage]
    model_result: dict[str, Any]
    response: dict[str, Any]


class AgentGraphOutput(TypedDict):
    response: dict[str, Any]
